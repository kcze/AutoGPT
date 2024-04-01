from __future__ import annotations

import inspect
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Iterator, Optional

import sentry_sdk
from pydantic import Field

from autogpt.agents.components import (
    Component,
    ComponentGroupError,
)
from autogpt.components.one_shot_component import OneShotComponent
from autogpt.core.configuration import Configurable
from autogpt.core.prompting import ChatPrompt
from autogpt.core.resource.model_providers import (
    AssistantChatMessage,
    ChatMessage,
    ChatModelProvider,
)
from autogpt.file_storage.base import FileStorage
from autogpt.llm.api_manager import ApiManager
from autogpt.llm.providers.openai import get_openai_command_specs
from autogpt.logs.log_cycle import (
    CURRENT_CONTEXT_FILE_NAME,
    NEXT_ACTION_FILE_NAME,
    USER_INPUT_FILE_NAME,
    LogCycleHandler,
)
from autogpt.logs.utils import fmt_kwargs
from autogpt.models.action_history import (
    Action,
    ActionErrorResult,
    ActionInterruptedByHuman,
    ActionResult,
    ActionSuccessResult,
)
from autogpt.models.command import Command, CommandOutput
from autogpt.agents.protocols import MessageProvider
from autogpt.components.system import SystemComponent
from autogpt.components.user_interaction import UserInteractionComponent
from autogpt.components.code_executor import CodeExecutorComponent
from autogpt.components.git_operations import GitOperationsComponent
from autogpt.components.image_gen import ImageGeneratorComponent
from autogpt.components.web_search import WebSearchComponent
from autogpt.components.web_selenium import WebSeleniumComponent
from autogpt.components.event_history import EventHistoryComponent
from autogpt.core.resource.model_providers.schema import (
    ChatModelResponse,
)
from autogpt.core.utils.json_schema import JSONSchema

from .base import (
    BaseAgent,
    BaseAgentConfiguration,
    BaseAgentSettings,
    ThoughtProcessOutput,
    retry,
)
from ..components.file_manager import FileManagerComponent
from ..components.context import ContextComponent
from ..components.watchdog import WatchdogComponent
from .utils.exceptions import (
    AgentException,
    AgentTerminated,
    CommandExecutionError,
    InvalidOperationError,
    UnknownCommandError,
)

if TYPE_CHECKING:
    from autogpt.config import Config

logger = logging.getLogger(__name__)


#TODO kcze merge config and settings?
class AgentConfiguration(BaseAgentConfiguration):
    pass


class AgentSettings(BaseAgentSettings):
    config: AgentConfiguration = Field(default_factory=AgentConfiguration)


#TODO kcze merge with SystemComponent
class ClockBudgetComponent(Component, MessageProvider):
    """Clock and budget messages."""

    def get_messages(
        self,
    ) -> Iterator[ChatMessage]:
        # Clock
        yield ChatMessage.system(f"The current time and date is {time.strftime('%c')}")

        # Add budget information (if any) to prompt
        api_manager = ApiManager()
        if api_manager.get_total_budget() > 0.0:
            remaining_budget = (
                api_manager.get_total_budget() - api_manager.get_total_cost()
            )
            if remaining_budget < 0:
                remaining_budget = 0
            # TODO kcze this is repeated similarly in constraints
            budget_msg = ChatMessage.system(
                f"Your remaining API budget is ${remaining_budget:.3f}"
                + (
                    " BUDGET EXCEEDED! SHUT DOWN!\n\n"
                    if remaining_budget == 0
                    else (
                        " Budget very nearly exceeded! Shut down gracefully!\n\n"
                        if remaining_budget < 0.005
                        else (
                            " Budget nearly exceeded. Finish up.\n\n"
                            if remaining_budget < 0.01
                            else ""
                        )
                    )
                ),
            )
            logger.debug(budget_msg)
            yield budget_msg


class Agent(BaseAgent, Configurable[AgentSettings]):
    default_settings: AgentSettings = AgentSettings(
        name="Agent",
        description=__doc__ if __doc__ else "",
    )

    def __init__(
        self,
        settings: AgentSettings,
        llm_provider: ChatModelProvider,
        file_storage: FileStorage,
        legacy_config: Config,
    ):
        super().__init__(settings, llm_provider)

        # Components
        self.system = SystemComponent(legacy_config, settings, self)
        self.extra = ClockBudgetComponent()
        self.history = EventHistoryComponent(
            settings.history,
            self.send_token_limit,
            lambda x: self.llm_provider.count_tokens(x, self.llm.name),
            legacy_config,
            llm_provider,
        )
        self.user_interaction = UserInteractionComponent(legacy_config)
        self.file_manager = FileManagerComponent(settings, file_storage)
        self.code_executor = CodeExecutorComponent(
            self.file_manager.workspace,
            settings,
            legacy_config,
        )
        self.git_ops = GitOperationsComponent(legacy_config)
        self.image_gen = ImageGeneratorComponent(
            self.file_manager.workspace, legacy_config
        )
        self.web_search = WebSearchComponent(legacy_config)
        self.web_selenium = WebSeleniumComponent(legacy_config, llm_provider, self.llm)
        self.context = ContextComponent(self.file_manager.workspace)
        self.watchdog = WatchdogComponent(settings.config, settings.history)
        self.prompt_strategy = OneShotComponent(
            settings, legacy_config, llm_provider, self.send_token_limit, self.llm
        )

        # Override component ordering
        self.components = [
            self.system,
            self.extra,
            self.user_interaction,
            self.file_manager,
            self.code_executor,
            self.history,
            self.git_ops,
            self.image_gen,
            self.web_search,
            self.web_selenium,
            self.context,
            self.watchdog,
            self.prompt_strategy,
        ]

        self.created_at = datetime.now().strftime("%Y%m%d_%H%M%S")
        """Timestamp the agent was created; only used for structured debug logging."""

        self.log_cycle_handler = LogCycleHandler()
        """LogCycleHandler for structured debug logging."""

        self.event_history = settings.history
        self.legacy_config = legacy_config

    async def propose_action(self) -> ThoughtProcessOutput:
        """Proposes the next action to execute, based on the task and current state.

        Returns:
            The command name and arguments, if any, and the agent's thoughts.
        """
        self.reset_trace()

        # TODO kcze update directives

        # Get commands
        # TODO kcze self is temporary measure to access commands in execute
        self.commands: list[Command] = list(
            await self.foreach_components("get_commands")
        )

        # Get messages
        messages: list[ChatMessage] = list(
            await self.foreach_components("get_messages")
        )

        messages.append(
            ChatMessage.user(
                "Determine exactly one command to use next based on the given goals "
                "and the progress you have made so far, "
                "and respond using the JSON schema specified previously."
            )
        )

        prompt = ChatPrompt(
            messages=messages,
            functions=get_openai_command_specs(self.commands),
        )

        # TODO kcze before completion
        self.log_cycle_handler.log_count_within_cycle = 0
        self.log_cycle_handler.log_cycle(
            self.state.ai_profile.ai_name,
            self.created_at,
            self.config.cycle_count,
            prompt.raw(),
            CURRENT_CONTEXT_FILE_NAME,
        )

        # logger.debug(f"Executing prompt:\n{dump_prompt(prompt)}")
        output = await self.complete_and_parse(prompt)
        self.config.cycle_count += 1

        self.print_trace()

        return output

    @retry()
    async def complete_and_parse(
        self, prompt: ChatPrompt, exception: Optional[Exception] = None
    ) -> ThoughtProcessOutput:
        if exception:
            prompt.messages.append(ChatMessage.system(f"Error: {exception}"))

        response: AssistantChatMessage = (
            await self.llm_provider.create_chat_completion_raw(
                prompt.messages,
                functions=(
                    get_openai_command_specs(self.commands)
                    if self.config.use_functions_api
                    else []
                ),
                model_name=self.llm.name,
            )
        )
        result = ThoughtProcessOutput()
        result: ThoughtProcessOutput = await self.foreach_components(
            "parse_response", result, response
        )

        # Check if the command is valid, e.g. isn't duplicating a previous command
        command = self.get_command(result.command_name)
        if command:
            is_valid, reason = command.is_valid(result.command_args)
            if not is_valid:
                raise InvalidOperationError(reason)

        self.log_cycle_handler.log_cycle(
            self.state.ai_profile.ai_name,
            self.created_at,
            self.config.cycle_count,
            result.thoughts,
            NEXT_ACTION_FILE_NAME,
        )

        await self.foreach_components("after_parsing", result)

        return result

    async def execute(
        self,
        command_name: str,
        command_args: dict[str, str] = {},
        user_input: str = "",
    ) -> ActionResult:
        result: ActionResult

        if command_name == "human_feedback":
            result = ActionInterruptedByHuman(feedback=user_input)
            self.log_cycle_handler.log_cycle(
                self.state.ai_profile.ai_name,
                self.created_at,
                self.config.cycle_count,
                user_input,
                USER_INPUT_FILE_NAME,
            )

        else:
            try:
                return_value = await self.execute_command(
                    command_name=command_name,
                    arguments=command_args,
                )

                result = ActionSuccessResult(outputs=return_value)
            except AgentTerminated:
                raise
            except AgentException as e:
                result = ActionErrorResult.from_exception(e)
                logger.warning(
                    f"{command_name}({fmt_kwargs(command_args)}) raised an error: {e}"
                )
                sentry_sdk.capture_exception(e)

            result_tlength = self.llm_provider.count_tokens(str(result), self.llm.name)
            if result_tlength > self.send_token_limit // 3:
                result = ActionErrorResult(
                    reason=f"Command {command_name} returned too much output. "
                    "Do not execute this command again with the same arguments."
                )

        await self.foreach_components("after_execution", result)

        self.print_trace()

        return result

    def print_trace(self):
        print("\n".join(self.trace))

    async def execute_command(
        self,
        command_name: str,
        arguments: dict[str, str],
    ) -> CommandOutput:
        """Execute the command and return the result

        Args:
            command_name (str): The name of the command to execute
            arguments (dict): The arguments for the command

        Returns:
            str: The result of the command
        """
        # Execute a native command with the same name or alias, if it exists
        if command := self.get_command(command_name):
            try:
                result = command(**arguments)
                if inspect.isawaitable(result):
                    return await result
                return result
            except AgentException:
                raise
            except Exception as e:
                raise CommandExecutionError(str(e))

        raise UnknownCommandError(
            f"Cannot execute command '{command_name}': unknown command."
        )

    def get_command(self, command_name: str) -> Optional[Command]:
        # TODO kcze update this logic to preserve command names as much as possible
        # currently latter commands just obscure earlier ones
        for command in reversed(self.commands):
            if command_name in command.names:
                return command
        return None

    def find_obscured_commands(self) -> list[Command]:
        seen_names = set()
        obscured_commands = []
        for command in reversed(self.commands):
            # If all of the command's names have been seen, it's obscured
            if seen_names.issuperset(command.names):
                obscured_commands.append(command)
            else:
                seen_names.update(command.names)
        return list(reversed(obscured_commands))

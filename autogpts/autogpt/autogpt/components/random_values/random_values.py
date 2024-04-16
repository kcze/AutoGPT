import random
import string
import uuid
from typing import Iterator

from lorem_text import lorem

from autogpts.autogpt.autogpt.agents.protocols import CommandProvider
from autogpts.autogpt.autogpt.command_decorator import command
from autogpts.autogpt.autogpt.core.utils.json_schema import JSONSchema
from autogpts.autogpt.autogpt.models.command import Command


class RandomValuesComponent(CommandProvider):
    """
    Random Values component for Auto-GPT.
    Ported from plugin made by: https://github.com/sidewaysthought
    """

    def __init__(self):
        pass

    def get_commands(self) -> Iterator[Command]:
        yield self.random_number
        yield self.generate_uuids
        yield self.generate_string
        yield self.generate_password
        yield self.generate_placeholder_text

    @command(
        parameters={
            "min": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The minimum value, default 0",
                required=False,
            ),
            "max": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The maximum value, default 65535",
                required=False,
            ),
            "count": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The number of random numbers to return (1-256), default 1",
                required=False,
            ),
        }
    )
    def random_number(
        self, min: int | str = 0, max: int | str = 65535, count: int | str = 1
    ) -> list[int]:
        """
        Return a random integer between min and max

        Args:
            min (int): The minimum value
            max (int): The maximum value
            count (int): The number of random numbers to return

        Returns:
            list[int]: array of generated numbers
        """

        # Type-check the arguments
        min = int(min)
        max = int(max)
        count = int(count)

        # Ensure min is less than smax
        if min > max:
            min, max = max, min

        # Test ranges
        if not (1 <= count <= 256):
            raise ValueError("count must be between 1 and 256")

        # Make random numbers
        random_numbers: list[int] = []
        for _ in range(count):
            random_numbers.append(random.randint(min, max))

        return random_numbers

    @command(
        parameters={
            "count": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The number of UUIDs to return (1-256), default 1",
                required=False,
            )
        }
    )
    def generate_uuids(self, count: int | str = 1) -> list[str]:
        """
        Return a UUID

        Args:
            count (int): The number of UUIDs to return

        Returns:
            list[str]: array of generated UUIDs
        """

        # Type-check the arguments
        count = int(count)

        # Make values sane
        if not (1 <= count <= 256):
            raise ValueError("count must be between 1 and 256")

        uuids: list[str] = []
        for _ in range(count):
            uuids.append(str(uuid.uuid4()))

        return uuids

    @command(
        parameters={
            "len": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The length of the string (1-65535), default 10",
                required=False,
            ),
            "count": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The number of strings to return (1-256), default 1",
                required=False,
            ),
        }
    )
    def generate_string(self, len: int | str = 10, count: int | str = 1) -> list[str]:
        """
        Return a random string

        Args:
            len (int): The length of the string
            count (int): The number of strings to return

        Returns:
            list[str]: array of generated strings
        """

        # Type-check the arguments
        len = int(len)
        count = int(count)

        # Range checks
        if not (1 <= count <= 256):
            raise ValueError("count must be between 1 and 65535")
        if not (1 <= len <= 65535):
            raise ValueError("len must be between 1 and 65535")

        # Do the thing
        strings: list[str] = []
        for _ in range(count):
            strings.append(
                "".join(random.choice(string.ascii_letters) for i in range(len))
            )

        return strings

    @command(
        parameters={
            "len": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The length of the password (1-65535), default 16",
                required=False,
            ),
            "count": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The number of passwords to return (1-256), default 1",
                required=False,
            ),
        }
    )
    def generate_password(self, len: int | str = 16, count: int | str = 1) -> list[str]:
        """
        Return a random password of letters, numbers, and punctuation

        Args:
            len (int): The length of the password
            count (int): The number of passwords to return

        Returns:
            list[str]: array of generated passwords
        """

        # Type-check the arguments
        len = int(len)
        count = int(count)

        # Make values sane
        if not (1 <= len <= 65535):
            raise ValueError("len must be between 6 and 65535")
        if not (1 <= count <= 256):
            raise ValueError("count must be between 1 and 65535")

        # Do the thing
        passwords = []
        for _ in range(count):
            passwords.append(
                "".join(
                    random.choice(
                        string.ascii_letters + string.digits + string.punctuation
                    )
                    for i in range(len)
                )
            )

        return passwords

    @command(
        names=["lorem_ipsum", "generate_random_words", "generate_placeholder_text"],
        parameters={
            "count": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The number of words to return (1-65535), default 1",
                required=False,
            )
        },
    )
    def generate_placeholder_text(self, count: int | str = 1) -> str:
        """
        Return a random sentence of lorem ipsum text

        Args:
            count (int): The number of words to return

        Returns:
            str: The generated text
        """

        # Type-check the arguments
        count = int(count)

        # Make values sane
        if not (1 <= count <= 65535):
            raise ValueError("count must be between 1 and 65535")

        return lorem.words(count)

from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

PublicId = Annotated[int, PlainSerializer(str, return_type=str, when_used="json")]


class WireModel(BaseModel):
    """Use model_dump(mode='json', by_alias=True); keep unknown values as null."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

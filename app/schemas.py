from typing import Literal

import pycountry
from pydantic import BaseModel, ConfigDict, Field, field_validator

COUNTRIES = {country.alpha_2: country.name for country in pycountry.countries}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CampaignCreate(Input):
    name: str = Field(min_length=1, max_length=180)
    country: str = Field(pattern=r"^[A-Za-z]{2}$")
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)

    @field_validator("country")
    @classmethod
    def uppercase(cls, value: str) -> str:
        code = value.upper()
        if code not in COUNTRIES:
            raise ValueError("Unknown ISO 3166-1 alpha-2 country code")
        return code


class Revision(Input):
    revision: int = Field(ge=1)


class OfferChange(Revision):
    action: Literal["add", "remove", "restore", "pin", "unpin"]
    offer_id: int = Field(gt=0)
    share: int | None = Field(default=None, ge=0, le=100)

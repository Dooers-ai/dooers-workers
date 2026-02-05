from enum import StrEnum
from typing import Any

from pydantic import BaseModel, model_validator


class SettingsFieldType(StrEnum):
    """Supported field types for worker settings."""

    TEXT = "text"
    NUMBER = "number"
    SELECT = "select"
    CHECKBOX = "checkbox"
    TEXTAREA = "textarea"
    PASSWORD = "password"
    EMAIL = "email"
    DATE = "date"
    IMAGE = "image"  # Display-only (QR codes, etc.)


class SettingsSelectOption(BaseModel):
    """Option for select fields."""

    value: str
    label: str


class SettingsField(BaseModel):
    """Definition of a single settings field."""

    id: str
    type: SettingsFieldType
    label: str
    required: bool = False
    readonly: bool = False
    value: Any = None  # Default value

    # Type-specific options
    placeholder: str | None = None
    options: list[SettingsSelectOption] | None = None  # For select
    min: int | float | None = None  # For number
    max: int | float | None = None  # For number
    rows: int | None = None  # For textarea
    src: str | None = None  # For image (display URL)
    width: int | None = None  # For image
    height: int | None = None  # For image


class SettingsSchema(BaseModel):
    """Schema definition for worker settings."""

    version: str = "1.0"
    fields: list[SettingsField]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "SettingsSchema":
        """Ensure all field IDs are unique."""
        ids = [f.id for f in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("Field IDs must be unique")
        return self

    def get_field(self, field_id: str) -> SettingsField | None:
        """Get a field by its ID."""
        for field in self.fields:
            if field.id == field_id:
                return field
        return None

    def get_defaults(self) -> dict[str, Any]:
        """Get default values for all fields."""
        return {f.id: f.value for f in self.fields}

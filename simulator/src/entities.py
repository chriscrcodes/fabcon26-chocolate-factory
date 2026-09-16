"""Static reference entities for the chocolate factory scenario.

Field names match fabric/ontology/ontology_config.json exactly, so the CSVs
written by seed_data.py can be dropped straight into a fabric-ontology
scenario folder without renaming.
"""

from dataclasses import dataclass


@dataclass
class Factory:
    FactoryId: str
    Code: str
    City: str
    Country: str
    Region: str
    TimeZone: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ProductionLine:
    LineId: str
    FactoryId: str
    LineNumber: int
    Name: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Recipe:
    RecipeId: str
    Name: str
    CacaoPercent: float
    MilkPercent: float
    SugarPercent: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()

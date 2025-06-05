from typing import Optional, List, Dict
from pydantic import BaseModel
from datetime import datetime

class DesignInCollection(BaseModel):
    design_id: str
    added_at: str

class CollectionCreate(BaseModel):
    name: str
    description: Optional[str] = None

class CollectionDesign(BaseModel):
    design_id: str

class Collection(BaseModel):
    user_email: str
    collection_name: str
    description: Optional[str] = None
    design_count: int
    created_at: str
    updated_at: str
    designs: List[DesignInCollection] = []
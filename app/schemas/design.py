import pydantic
from typing import Optional, List
from pydantic import BaseModel

class Design(pydantic.BaseModel):
    title: str
    price: float
    category: str
    dpi: int
    metadata: str
    # design_file: UploadFile
    # thumbnail_file: UploadFile

class ApproveDesignRequest(BaseModel):
    verification_comments: Optional[str] = None
    modified_category: Optional[str] = None
    modified_tags: Optional[str] = None
    modified_layers: Optional[str] = None
    is_color_matching: Optional[bool] = None
    color_matching_design_id: Optional[str] = None

class BundleDiscountRequest(BaseModel):
    bundle_discount: Optional[float] = None


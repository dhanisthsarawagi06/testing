from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from datetime import datetime
from app.services.storage import upload_design_files
from app.services.auth import get_current_user, verify_designer_status
from typing import Optional, Dict, Any
from app.schemas.design import ApproveDesignRequest, BundleDiscountRequest
from app.utils.user import get_username_from_email
import json
from app.services import dynamodb as dynamodb_service
from app.utils.s3_utils import S3Handler
from fastapi.responses import JSONResponse
import os
from pydantic import BaseModel
from app.services.image_search import ImageSearchService
from app.services.text_search import TextSearchService
import base64
import logging
import io
from PIL import Image

DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')
TRANSACTION_TABLE = os.getenv('DYNAMODB_TRANSACTION_TABLE')
USER_TABLE = os.getenv('DYNAMODB_USER_TABLE')
router = APIRouter()

# Set up logging
logger = logging.getLogger(__name__)

# Define request model for base64 image
class ImageSearchRequest(BaseModel):
    image_base64: str
    page: int = 1
    limit: int = 20

@router.post("/upload")
async def upload_design(
    design_file: UploadFile = File(...),
    thumbnail_file: UploadFile = File(...),
    title: str = Form(...),
    price: float = Form(...),
    category: str = Form(...),
    dpi: int = Form(...),
    metadata: str = Form(...),
    tags: str = Form(...),
    payment_method: str = Form(...),
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """This endpoint is not used anymore. The new endpoint is generate-upload-urls and create."""

    try:
        # Upload files to S3
        design_url, thumbnail_url = await upload_design_files(
            design_file=design_file,
            metadata=metadata,
            thumbnail_file=thumbnail_file,
            title=title,
            user_email=current_user["email"]
        )

        # Generate unique design ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = title.replace(' ', '_').replace('/', '-').replace('\\', '-').lower()
        design_id = f"{safe_title}_{timestamp}"
        
        # Parse metadata
        design_metadata = json.loads(metadata)
        
        # Prepare DynamoDB item
        design_item = {
            'design_id': {'S': design_id},
            'title': {'S': title},
            'price': {'N': str(price)},
            'category': {'S': category},
            'tags': {'S': tags},
            'dpi': {'N': str(dpi)},
            'design_url': {'S': design_url},
            'thumbnail_url': {'S': thumbnail_url},
            'seller_email': {'S': current_user['email']},
            'verification_status': {'S': 'Pending'},
            # 'verified_by': {'S': ''},
            # 'verified_at': {'S': ''},
            # 'verification_comments': {'S': ''},
            'created_at': {'S': str(datetime.now())},
            'metadata': {'S': metadata},
            # 'filetype': {'S': design_file.content_type},
            # 'size': {'N': str(design_file.size)},
            'total_sold': {'N': '0'},
            'payment_method': {'S': payment_method}
        }

        # Save to DynamoDB
        response = dynamodb.put_item(
            TableName=DESIGN_TABLE,
            Item=design_item
        )

        return {
            "design_id": design_id,
            "design_url": design_url,
            "thumbnail_url": thumbnail_url,
            "message": "Design uploaded successfully"
        }

    except Exception as e:
        print(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/pending")
async def get_pending_designs(
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client),
    s3_handler = Depends(S3Handler)
):
    """Get all designs with verification_status = 'Pending'"""
    try:
        is_designer = await verify_designer_status(current_user, dynamodb)
        if not is_designer:
            raise HTTPException(
                status_code=403,
                detail="Only designers can view pending designs"
            )

        response = dynamodb.scan(
            TableName=DESIGN_TABLE,
            FilterExpression='verification_status = :status',
            ExpressionAttributeValues={
                ':status': {'S': 'Pending'}
            }
        )

        designs = []
        for item in response.get('Items', []):
            try:
                # Extract just the path part from the full S3 URL
                design_key = item['design_url']['S'].split('amazonaws.com/')[-1]
                thumbnail_key = item['thumbnail_url']['S'].split('amazonaws.com/')[-1]
                
                design_url = await s3_handler.generate_presigned_url(
                    s3_handler.designs_bucket,
                    design_key
                )
                thumbnail_url = await s3_handler.generate_presigned_url(
                    s3_handler.thumbnails_bucket,
                    thumbnail_key
                )

                # Get original design details if this is a color matching design
                color_matching_info = None
                if item.get('is_color_matching', {}).get('BOOL', False):
                    original_design_id = item.get('color_matching_design_id', {}).get('S')
                    if original_design_id:
                        original_design = dynamodb.get_item(
                            TableName=DESIGN_TABLE,
                            Key={'design_id': {'S': original_design_id}}
                        ).get('Item')
                        
                        if original_design:
                            original_thumbnail_key = original_design['thumbnail_url']['S'].split('amazonaws.com/')[-1]
                            original_thumbnail_url = await s3_handler.generate_presigned_url(
                                s3_handler.thumbnails_bucket,
                                original_thumbnail_key
                            )
                            
                            color_matching_info = {
                                'original_design_id': original_design_id,
                                'original_title': original_design['title']['S'],
                                'original_thumbnail_url': original_thumbnail_url,
                                'original_category': original_design['category']['S'],
                                'original_status': original_design['verification_status']['S']
                            }

                # Parse metadata for file type and resolution
                try:
                    metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                    layers = metadata.get('layers', 0)
                except (json.JSONDecodeError, AttributeError):
                    metadata = {}
                    layers = 0

                designs.append({
                    'id': item['design_id']['S'],
                    'title': item['title']['S'],
                    'design_url': design_url,
                    'thumbnail_url': thumbnail_url,
                    'created_at': item.get('created_at', {}).get('S'),
                    'seller_email': item.get('seller_email', {}).get('S'),
                    'category': item.get('category', {}).get('S', ''),
                    'tags': item.get('tags', {}).get('S', '').split(',') if item.get('tags', {}).get('S') else [],
                    'is_color_matching': item.get('is_color_matching', {}).get('BOOL', False),
                    'color_matching_info': color_matching_info,
                    'metadata': metadata,
                    'layers': layers
                })
            except Exception as e:
                print(f"Error processing design item: {str(e)}")
                continue

        return JSONResponse(content={"designs": designs})
    except HTTPException as he:
        # Re-raise HTTP exceptions as they are already properly formatted
        raise he
    except Exception as e:
        print(f"Error in pending designs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/approve")
async def approve_design(
    design_id: str,
    request: ApproveDesignRequest,
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """Approve a design with optional modifications"""
    # Check designer status first
    is_designer = await verify_designer_status(current_user, dynamodb)
    if not is_designer:
        raise HTTPException(
            status_code=403,
            detail="Only designers can approve designs"
        )

    try:
        # Check if design exists
        design_check = dynamodb.get_item(
            TableName=DESIGN_TABLE,
            Key={'design_id': {'S': design_id}}
        )
        
        if 'Item' not in design_check:
            raise HTTPException(
                status_code=404,
                detail="Design not found"
            )

        # Start with base update expression
        update_expr = 'SET verification_status = :status, verified_by = :approver, verified_at = :time, is_color_matching = :is_color_matching, color_matching_design_id = :color_matching_design_id'
        expr_attrs = {
            ':status': {'S': 'Verified'},
            ':approver': {'S': current_user['email']},
            ':time': {'S': str(datetime.now())},
            ':is_color_matching': {'BOOL': request.is_color_matching},
            ':color_matching_design_id': {'S': request.color_matching_design_id}
        }

        # Add verification comments if present
        if request.verification_comments:
            update_expr += ', verification_comments = :comments'
            expr_attrs[':comments'] = {'S': request.verification_comments}

        # Add category modification if present
        if request.modified_category:
            update_expr += ', category = :category'
            expr_attrs[':category'] = {'S': request.modified_category}

        # Add or update tags if present
        if request.modified_tags:
            update_expr += ', tags = :tags'
            # Parse the JSON string into a list
            tags_list = json.loads(request.modified_tags)
            expr_attrs[':tags'] = {'S': ','.join(tags_list)}
        elif 'tags' not in design_check['Item']:
            # If no tags exist and no modifications, create empty tags field
            update_expr += ', tags = :tags'
            expr_attrs[':tags'] = {'S': ''}

        # Add layer modifications if present
        if request.modified_layers:
            layers_data = json.loads(request.modified_layers)
            # Update metadata with new layer information
            try:
                metadata = json.loads(design_check['Item'].get('metadata', {}).get('S', '{}'))
                metadata['layers'] = layers_data
                update_expr += ', metadata = :metadata'
                expr_attrs[':metadata'] = {'S': json.dumps(metadata)}
            except (json.JSONDecodeError, AttributeError):
                # If metadata parsing fails, create new metadata with just layers
                update_expr += ', metadata = :metadata'
                expr_attrs[':metadata'] = {'S': json.dumps({'layers': layers_data})}

        # Update DynamoDB
        response = dynamodb.update_item(
            TableName=DESIGN_TABLE,
            Key={'design_id': {'S': design_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_attrs
        )

        return JSONResponse(
            content={"message": "Design approved successfully"},
            status_code=200
        )

    except HTTPException as he:
        print(f"Error approving design: {str(he)}")
        raise he
    except Exception as e:
        print(f"Error approving design: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@router.post("/reject")
async def reject_design(
    design_id: str,
    verification_comments: str,
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """Reject a design with comments"""
    # Check designer status first
    is_designer = await verify_designer_status(current_user, dynamodb)
    if not is_designer:
        raise HTTPException(
            status_code=403,
            detail="Only designers can reject designs"
        )

    try:
        # Check if design exists
        design_check = dynamodb.get_item(
            TableName=DESIGN_TABLE,
            Key={'design_id': {'S': design_id}}
        )
        
        if 'Item' not in design_check:
            raise HTTPException(
                status_code=404,
                detail="Design not found"
            )

        response = dynamodb.update_item(
            TableName=DESIGN_TABLE, 
            Key={'design_id': {'S': design_id}},
            UpdateExpression='SET verification_status = :status, verified_by = :rejector, verified_at = :time, verification_comments = :comments',
            ExpressionAttributeValues={
                ':status': {'S': 'Rejected'},
                ':rejector': {'S': current_user['email']},
                ':time': {'S': str(datetime.now())},
                ':comments': {'S': verification_comments}
            }
        )

        return JSONResponse(
            content={"message": "Design rejected successfully"},
            status_code=200
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error rejecting design: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reject design: {str(e)}"
        )

@router.get("/download/{design_id}")
async def get_design_download_url(
    design_id: str,
    current_user: dict = Depends(get_current_user),
    s3_handler = Depends(S3Handler),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """Get a presigned URL for downloading a purchased design"""
    try:
        print('=== get_design_download_url START ===')
        # Check if user is a designer
        is_designer = await verify_designer_status(current_user, dynamodb)
        
        if not is_designer:
            # For regular users, check if they have purchased the design
            purchase_response = dynamodb.query(
                TableName=TRANSACTION_TABLE,
                IndexName='buyer_email-created_at-index',
                KeyConditionExpression='buyer_email = :email',
                ExpressionAttributeValues={
                    ':email': {'S': current_user['email']},
                    ':status': {'S': 'COMPLETED'}
                },
                FilterExpression='#transaction_status = :status',
                ExpressionAttributeNames={
                    '#transaction_status': 'status'
                }
            )

            # Check if the design exists in any completed transaction
            design_found = False
            for transaction in purchase_response.get('Items', []):
                for design in transaction['designs']['L']:
                    if design['M']['design_id']['S'] == design_id:
                        design_found = True
                        break
                if design_found:
                    break

            if not design_found:
                raise HTTPException(
                    status_code=403,
                    detail="You haven't purchased this design"
                )

        # Get design details from DynamoDB
        response = dynamodb.get_item(
            TableName=DESIGN_TABLE,
            Key={'design_id': {'S': design_id}}
        )

        if 'Item' not in response:
            raise HTTPException(status_code=404, detail="Design not found")

        design = response['Item']
        design_key = design['design_url']['S'].split('amazonaws.com/')[-1]
        # Generate presigned URL for download
        download_url = await s3_handler.generate_presigned_url(
            s3_handler.designs_bucket,
            design_key,
            expiration=3600  # URL expires in 1 hour
        )

        if not download_url:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate download URL"
            )

        return JSONResponse(content={"download_url": download_url})

    except HTTPException as he:
        raise he
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/home")
async def get_home_designs(
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """Get verified designs for home page, no auth required"""
    try:
        response = dynamodb.scan(
            TableName=DESIGN_TABLE,
            FilterExpression='verification_status = :status',
            ExpressionAttributeValues={
                ':status': {'S': 'Verified'}
            }
        )

        # Group designs by category
        designs_by_category = {}
        for item in response.get('Items', []):
            
            category = item['category']['S']
            if category not in designs_by_category:
                designs_by_category[category] = []
            
            #Get seller username
            seller_username = await get_username_from_email(item['seller_email']['S'], dynamodb)

            # Parse metadata for file type and resolution
            try:
                metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                file_type = metadata.get('fileType', '').upper() if 'fileType' in metadata else metadata.get('type', '').split('/')[-1].upper()
                dimensions = metadata.get('dimensions', {})
                resolution = f"{dimensions.get('width', '')}x{dimensions.get('height', '')}" if dimensions else ''
                layers = metadata.get('layers',0)
            except (json.JSONDecodeError, AttributeError):
                file_type = ''
                resolution = ''
                layers = 0
            
            designs_by_category[category].append({
                'id': item['design_id']['S'],
                'title': item['title']['S'],
                'thumbnail_url': item['thumbnail_url']['S'],
                'price': float(item['price']['N']),
                'category': category,
                'created_at': item.get('created_at', {}).get('S'),
                'file_type': file_type,
                'resolution': resolution,
                'layers': layers,
                'is_color_matching': item.get('is_color_matching', {}).get('BOOL', False),
                'color_matching_design_id': item.get('color_matching_design_id', {}).get('S', ''),
                'seller_username': seller_username,
                'bundle_discount': item.get('bundle_discount', {}).get('N', '0')
            })

        return {"designs": designs_by_category}

    except Exception as e:
        print(f"Error fetching verified designs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/gallery/{category}")
async def get_gallery_designs(
    category: str,
    page: int = 1,
    limit: int = 50,
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """Get verified designs for a specific category with pagination"""
    try:
        response = dynamodb.scan(
            TableName=DESIGN_TABLE,
            FilterExpression='verification_status = :status AND category = :category',
            ExpressionAttributeValues={
                ':status': {'S': 'Verified'},
                ':category': {'S': category.replace('-', ' ').title()}
            }
        )

        # Process and filter designs
        all_designs = []
        for item in response.get('Items', []):

            #Get seller username
            seller_username = await get_username_from_email(item['seller_email']['S'], dynamodb)

            # Parse metadata for file type and resolution
            try:
                metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                file_type = metadata.get('fileType', '').upper() if 'fileType' in metadata else metadata.get('type', '').split('/')[-1].upper()
                dimensions = metadata.get('dimensions', {})
                resolution = f"{dimensions.get('width', '')}x{dimensions.get('height', '')}" if dimensions else ''
                layers = metadata.get('layers',0)
            except (json.JSONDecodeError, AttributeError):
                file_type = ''
                resolution = ''
                layers = 0

            all_designs.append({
                'id': item['design_id']['S'],
                'title': item['title']['S'],
                'thumbnail_url': item['thumbnail_url']['S'],
                'price': float(item['price']['N']),
                'category': item['category']['S'],
                'created_at': item.get('created_at', {}).get('S'),
                'file_type': file_type,
                'resolution': resolution,
                'layers': layers,
                'seller_username': seller_username,
            })

        # Calculate pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit

        # Slice designs for pagination
        designs = all_designs[start_idx:end_idx]

        return {
            "designs": designs,
            "total": len(all_designs),
            "page": page,
            "limit": limit
        }

    except Exception as e:
        print(f"Error fetching gallery designs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search/image")
async def search_by_image(
    request: ImageSearchRequest,
    image_search_service: ImageSearchService = Depends(ImageSearchService)
):
    try:
        logger.info(f"Starting base64 image search request - page: {request.page}, limit: {request.limit}")
        
        # Decode base64 image without using PIL
        try:
            image_data = base64.b64decode(request.image_base64)
            logger.info(f"Successfully decoded base64 image - size: {len(image_data)} bytes")
        except Exception as decode_error:
            logger.error(f"Base64 decode error: {str(decode_error)}")
            raise HTTPException(status_code=400, detail=f"Invalid base64 encoding: {str(decode_error)}")
        
        # Perform image search with raw decoded data
        results = await image_search_service.search_similar_designs(
            image_content=image_data,
            page=request.page,
            limit=request.limit
        )
        
        return results
    except HTTPException as he:
        logger.error(f"HTTP Exception in image search: {str(he)}")
        raise
    except Exception as e:
        logger.error(f"Image search error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Image search failed: {str(e)}")
        
@router.get("/search")
async def search_designs(
    query: str,
    category: Optional[str] = None,
    dynamodb = Depends(dynamodb_service._get_dynamodb_client),
    text_search_service: TextSearchService = Depends(TextSearchService)
):
    try:
        logger.info(f"Starting search with query: {query}, category: {category}")
        
        # First check if query is a design_id
        if query and query.strip():  # Only check if query is not empty
            design_id_response = dynamodb.get_item(
                TableName=DESIGN_TABLE,
                Key={'design_id': {'S': query}}
            )
            
            # If design_id exists, return only that result
            if 'Item' in design_id_response:
                item = design_id_response['Item']
                metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                seller_username = await get_username_from_email(item['seller_email']['S'], dynamodb)
                
                return {
                    "designs": [{
                        'id': item['design_id']['S'],
                        'title': item['title']['S'],
                        'thumbnail_url': item['thumbnail_url']['S'],
                        'price': float(item['price']['N']),
                        'category': item['category']['S'],
                        'tags': item.get('tags', {}).get('S', '').split(','),
                        'seller_username': seller_username,
                        'file_type': metadata.get('fileType', '').upper(),
                        'resolution': f"{metadata.get('dimensions', {}).get('width', '')}x{metadata.get('dimensions', {}).get('height', '')}",
                        'layers': metadata.get('layers', 0),
                        'is_color_matching': item.get('is_color_matching', {}).get('BOOL', False),
                        'color_matching_design_id': item.get('color_matching_design_id', {}).get('S', '')
                    }]
                }

        filter_expr = 'verification_status = :status'
        expr_values = {
            ':status': {'S': 'Verified'}
        }
        
        response = dynamodb.scan(
            TableName=DESIGN_TABLE,
            FilterExpression=filter_expr,
            ExpressionAttributeValues=expr_values
        )

        # Process designs and filter by search query
        designs = []
        query_lower = query.lower()
        
        for item in response.get('Items', []):
            # Check if title, category or tags contain search query (case-insensitive)
            title = item['title']['S'].lower()
            item_category = item['category']['S'].lower()
            tags = item.get('tags', {}).get('S', '').lower()
            
            if not (query_lower in title or 
                   query_lower in item_category or 
                   query_lower in tags):
                continue
                
            seller_username = await get_username_from_email(item['seller_email']['S'], dynamodb)

            try:
                metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                file_type = metadata.get('fileType', '').upper()
                dimensions = metadata.get('dimensions', {})
                resolution = f"{dimensions.get('width', '')}x{dimensions.get('height', '')}"
                layers = metadata.get('layers',0)
            except (json.JSONDecodeError, AttributeError):
                file_type = ''
                resolution = ''
                layers = 0
            
            designs.append({
                'id': item['design_id']['S'],
                'title': item['title']['S'],
                'thumbnail_url': item['thumbnail_url']['S'],
                'price': float(item['price']['N']),
                'category': item['category']['S'],
                'tags': item.get('tags', {}).get('S', '').split(','),
                'seller_username': seller_username,
                'file_type': file_type,
                'resolution': resolution,
                'layers': layers,
                'is_color_matching': item.get('is_color_matching', {}).get('BOOL', False),
                'color_matching_design_id': item.get('color_matching_design_id', {}).get('S', '')
            })

        final_results = designs

        # Try AI search only if DynamoDB returns less than 8 results
        if len(designs) < 8:
            try:
                logger.info("Not enough DynamoDB results, attempting AI search")
                ai_response = await text_search_service.search_similar_designs(query)
                
                # Add non-duplicate AI results
                seen_ids = {d['id'] for d in designs}
                for result in ai_response['results']:
                    if result['id'] not in seen_ids and len(final_results) < 20:
                        final_results.append(result)
                        seen_ids.add(result['id'])
            except Exception as e:
                logger.error(f"AI search failed, continuing with DynamoDB results: {str(e)}")
                # Continue with just the DynamoDB results if AI search fails

        return {
            "designs": final_results,
        }

    except Exception as e:
        logger.error(f"Search failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-upload-urls")
async def generate_upload_urls(
    file_info: dict,
    current_user: dict = Depends(get_current_user),
    s3_handler = Depends(S3Handler)
):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = file_info['title'].replace(' ', '_').replace('/', '-').replace('\\', '-').lower()
        
        # Generate unique design ID
        design_id = f"{safe_title}_{timestamp}"
        
        # Generate unique paths for both files
        design_path = f"{current_user['email']}/{design_id}.{file_info['designType']}"
        thumbnail_path = f"{current_user['email']}/{design_id}.jpg"
        
        # Generate presigned URLs for both files
        design_url = await s3_handler.generate_presigned_post(
            s3_handler.designs_bucket,
            design_path,
            file_info['designContentType']
        )
        print(design_url)
        
        thumbnail_url = await s3_handler.generate_presigned_post(
            s3_handler.thumbnails_bucket,
            thumbnail_path,
            'image/jpeg'
        )
        print(thumbnail_url)

        return {
            "design_upload": design_url,
            "thumbnail_upload": thumbnail_url,
            "design_key": design_path,
            "thumbnail_key": thumbnail_path,
            "design_id": design_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/create")
async def create_design(
    request: dict,
    s3_handler = Depends(S3Handler),
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    try:
        # Generate unique design ID
        # timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # safe_title = request['title'].replace(' ', '_').replace('/', '-').replace('\\', '-').lower()
        # design_id = f"{safe_title}_{timestamp}"
        design_id = request['design_id']
        # Generate full S3 URLs
        design_url = f"https://{s3_handler.designs_bucket}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{request['design_key']}"
        thumbnail_url = f"https://{s3_handler.thumbnails_bucket}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{request['thumbnail_key']}"
        
        tags_list = json.loads(request['tags'])
        tags = ','.join(tags_list)
        # Prepare DynamoDB item
        design_item = {
            'design_id': {'S': design_id},
            'title': {'S': request['title']},
            'price': {'N': str(request['price'])},
            'category': {'S': request['category']},
            'tags': {'S': tags},
            'dpi': {'N': str(request['dpi'])},
            'design_url': {'S': design_url},
            'thumbnail_url': {'S': thumbnail_url},
            'seller_email': {'S': current_user['email']},
            'verification_status': {'S': 'Pending'},
            'created_at': {'S': str(datetime.now())},
            'metadata': {'S': request['metadata']},
            'total_sold': {'N': '0'},
            'payment_method': {'S': request['payment_method']},
            'color_matching_design_id': {'S': request.get('color_matching_design_id','')},
            'is_color_matching':{'BOOL':request.get('is_color_matching',False)},
            'bundle_discount': {'N': str(request.get('bundle_discount',0))}
        }

        # Save to DynamoDB
        response = dynamodb.put_item(
            TableName=DESIGN_TABLE,     
            Item=design_item
        )

        return {
            "design_id": design_id,
            "message": "Design record created successfully"
        }

    except Exception as e:
        print(f"Error creating design record: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/seller/{username}")
async def get_seller_designs(
    username: str,
    page: int = 1,
    limit: int = 20,
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    try:
        # Get seller email by querying USER_TABLE with username
        user_response = dynamodb.scan(
            TableName=USER_TABLE,
            FilterExpression='username = :username',
            ExpressionAttributeValues={
                ':username': {'S': username}
            }
        )
        
        user_items = user_response.get('Items', [])
        if not user_items:
            raise HTTPException(status_code=404, detail="Seller not found")
            
        seller_email = user_items[0]['email']['S']
        
        # Query designs using GSI with just the partition key
        response = dynamodb.query(
            TableName=DESIGN_TABLE,
            IndexName='DesignSellerGSI',
            KeyConditionExpression='seller_email = :email',
            FilterExpression='verification_status = :status',
            ExpressionAttributeValues={
                ':email': {'S': seller_email},
                ':status': {'S': 'Verified'}
            }
        )

        designs = []
        for item in response.get('Items', []):
            # Skip color matching designs if original is not verified
            if item.get('is_color_matching', {}).get('BOOL', False):
                original_id = item.get('color_matching_design_id', {}).get('S')
                if original_id:
                    original_design = dynamodb.get_item(
                        TableName=DESIGN_TABLE,
                        Key={'design_id': {'S': original_id}}
                    ).get('Item')
                    
                    if not original_design or original_design.get('verification_status', {}).get('S') != 'Verified':
                        continue

            # Parse metadata for file type and resolution
            try:
                metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                file_type = metadata.get('fileType', '').upper()
                dimensions = metadata.get('dimensions', {})
                resolution = f"{dimensions.get('width', '')}x{dimensions.get('height', '')}"
                layers = metadata.get('layers', 0)
            except (json.JSONDecodeError, AttributeError):
                file_type = ''
                resolution = ''
                layers = 0

            designs.append({
                'id': item['design_id']['S'],
                'title': item['title']['S'],
                'price': float(item['price']['N']),
                'thumbnail_url': item['thumbnail_url']['S'],
                'category': item['category']['S'],
                'tags': item.get('tags', {}).get('S', '').split(','),
                'file_type': file_type,
                'resolution': resolution,
                'layers': layers,
                'is_color_matching': item.get('is_color_matching', {}).get('BOOL', False),
                'color_matching_design_id': item.get('color_matching_design_id', {}).get('S', '')
            })

        # Sort designs by most recent first
        designs.sort(key=lambda x: x.get('id', ''), reverse=True)
        
        # Calculate pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_designs = designs[start_idx:end_idx]

        return {
            "designs": paginated_designs,
            "total": len(designs)
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error fetching seller designs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{design_id}/bundle-discount")
async def update_bundle_discount(
    design_id: str,
    request: BundleDiscountRequest,
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """Update bundle discount for a design with color variants"""
    try:
        # Check if design exists and get current data
        design_response = dynamodb.get_item(
            TableName=DESIGN_TABLE,
            Key={'design_id': {'S': design_id}}
        )
        
        if 'Item' not in design_response:
            raise HTTPException(
                status_code=404,
                detail="Design not found"
            )

        design_item = design_response['Item']

        # Verify ownership
        if design_item['seller_email']['S'] != current_user['email']:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to update this design"
            )

        # Verify the design has color variants
        # Check both as original and as variant
        variants_response = dynamodb.scan(
            TableName=DESIGN_TABLE,
            FilterExpression='color_matching_design_id = :original_id OR design_id = :original_id',
            ExpressionAttributeValues={
                
                ':original_id': design_item.get('color_matching_design_id', {'S': design_id}),
            }
        )
        print("variants_response",variants_response)

        # Validate discount range
        if not 0 <= request.bundle_discount <= 100:
            raise HTTPException(
                status_code=400,
                detail="Discount must be between 0 and 100"
            )

        # Update the bundle discount
        update_response = dynamodb.update_item(
            TableName=DESIGN_TABLE,
            Key={'design_id': {'S': design_id}},
            UpdateExpression='SET bundle_discount = :discount',
            ExpressionAttributeValues={
                ':discount': {'N': str(request.bundle_discount)}
            },
            ReturnValues='ALL_NEW'
        )

        # Also update the bundle discount for all variants
        if design_item.get('is_color_matching', {}).get('BOOL', False):
            # If this is a variant, update the original and other variants
            original_id = design_item['color_matching_design_id']['S']
            # Update original
            dynamodb.update_item(
                TableName=DESIGN_TABLE,
                Key={'design_id': {'S': original_id}},
                UpdateExpression='SET bundle_discount = :discount',
                ExpressionAttributeValues={
                    ':discount': {'N': str(request.bundle_discount)}
                }
            )
            # Update other variants
        if variants_response.get('Items'):
            for variant in variants_response['Items']:
                if variant['design_id']['S'] != design_id:
                    dynamodb.update_item(
                        TableName=DESIGN_TABLE,
                        Key={'design_id': {'S': variant['design_id']['S']}},
                        UpdateExpression='SET bundle_discount = :discount',
                        ExpressionAttributeValues={
                            ':discount': {'N': str(request.bundle_discount)}
                        }
                    )

        return JSONResponse(
            content={
                "message": "Bundle discount updated successfully",
                "bundle_discount": request.bundle_discount
            },
            status_code=200
        )

    except HTTPException as he:
        print(f"Error updating bundle discount: {str(he)}")
        raise he
    except Exception as e:
        print(f"Error updating bundle discount: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@router.get("/{design_id}/variants")
async def get_design_variants(
    design_id: str,
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    try:
        # Get all variants including the original design
        variants_response = dynamodb.scan(
            TableName=DESIGN_TABLE,
            FilterExpression='color_matching_design_id = :design_id AND is_color_matching = :true AND verification_status = :status',
            ExpressionAttributeValues={
                ':design_id': {'S': design_id},
                ':true': {'BOOL': True},
                ':status': {'S': 'Verified'}
            }
        )
       

        variants = []
        for item in variants_response.get('Items', []):
            if item['design_id']['S'] != design_id:  # Don't include the current design
                variants.append({
                    'id': item['design_id']['S'],
                    'title': item['title']['S'],
                    'price': float(item['price']['N']),
                    'thumbnail_url': item['thumbnail_url']['S'],
                    'is_color_matching': item.get('is_color_matching', {}).get('BOOL', False),
                    'color_matching_design_id': item.get('color_matching_design_id', {}).get('S'),
                    'bundle_discount': float(item.get('bundle_discount', {}).get('N', '0'))
                })
        

        # Add the original design to the variants
        original_design_response = dynamodb.get_item(
            TableName=DESIGN_TABLE,
            Key={'design_id': {'S': design_id}}
        )
        original_design = {
            'id': design_id,
            'title': original_design_response['Item']['title']['S'],
            'price': float(original_design_response['Item']['price']['N']),
            'thumbnail_url': original_design_response['Item']['thumbnail_url']['S'],
            'is_color_matching': original_design_response['Item'].get('is_color_matching', {}).get('BOOL', False),
            'color_matching_design_id': original_design_response['Item'].get('color_matching_design_id', {}).get('S'),
            'bundle_discount': float(original_design_response['Item'].get('bundle_discount', {}).get('N', '0'))
        }
        variants.append(original_design)

        print(variants)

        return {
            'variants': variants
        }

    except Exception as e:
        print(f"Error fetching variants: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

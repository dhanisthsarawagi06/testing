from typing import List, Optional
import boto3
import os
import json
from fastapi import HTTPException
import base64
from .dynamodb import _get_dynamodb_client
from datetime import datetime
import time
from ..utils.s3_utils import S3Handler
import logging
from app.utils.user import get_username_from_email
import dotenv

# Load environment variables
dotenv.load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)

class ImageSearchService:
    def __init__(self):
        logger.info("Initializing ImageSearchService")
        self.lambda_endpoint = os.getenv('SEARCH_LAMBDA_ENDPOINT')
        self.design_table = os.getenv('DYNAMODB_DESIGN_TABLE')
        self.aws_region = os.getenv('AWS_REGION')
        if not self.aws_region:
            raise ValueError("AWS_REGION must be set in environment variables")
        
        # Initialize AWS clients with region
        self.lambda_client = boto3.client('lambda', region_name=self.aws_region)
        self.dynamodb = _get_dynamodb_client()
        self.s3_handler = S3Handler()
        logger.info(f"Service initialized with lambda endpoint: {self.lambda_endpoint} in region: {self.aws_region}")

    async def search_similar_designs(self, image_content: bytes, page: int = 1, limit: int = 20) -> dict:
        search_key = None
        try:
            # Validate input
            if not image_content:
                raise HTTPException(
                    status_code=400,
                    detail="No image content provided"
                )
                
            logger.info("Successfully uploaded search image to S3")

            image_data = image_content
            search_image_url = base64.b64encode(image_data).decode("utf-8")
            # Save to verify image is valid

            logger.info(f"Generated base64 string: {search_image_url[:50]}...") # Log a snippet to avoid flooding logs

            # Call Lambda function using the initialized client
            logger.info("Preparing Lambda function call")
            payload = {
                "httpMethod": "POST",
                "body": json.dumps({
                    "path": "/search",
                    "base64": f"{search_image_url}",
                    "text": "",
                    "org_id": "textile-marketplace",
                    "collection_name": "image-search",
                    "userEmail": "search@system.internal",
                    "offset": (page - 1) * limit,
                    "limit": limit
                })
            }
            logger.info(f"Lambda payload prepared: {json.dumps(payload)}")

            try:
                response = await self._call_lambda(payload)
                logger.info(f"Raw Lambda response: {response}")
                
                # Parse lambda response
                if response.get('statusCode') != 200:
                    error_body = {}
                    try:
                        error_body = json.loads(response.get('body', '{}'))
                    except json.JSONDecodeError:
                        error_body = {'error': 'Unknown error occurred'}
                    
                    error_msg = error_body.get('error', 'Search failed')
                    logger.error(f"Lambda error response body: {error_body}")
                    
                    # Handle specific error cases
                    if 'Modal API error' in error_msg:
                        raise HTTPException(
                            status_code=503,
                            detail="The image search service is temporarily unavailable. Please try again in a few minutes."
                        )
                    elif 'SageMaker endpoint configuration missing' in error_msg:
                        raise HTTPException(
                            status_code=503,
                            detail="The search service is not properly configured. Please contact support."
                        )
                    elif 'Collection not found' in error_msg:
                        raise HTTPException(
                            status_code=404,
                            detail="The search collection is not available. Please try again later."
                        )
                    else:
                        raise HTTPException(
                            status_code=response.get('statusCode', 500),
                            detail=error_msg
                        )

                # Parse the response body
                try:
                    if isinstance(response['body'], str):
                        response_body = json.loads(response['body'])
                    else:
                        response_body = response['body']
                    logger.info(f"Parsed response body: {response_body}")
                    
                    # Handle both list and dictionary response formats
                    if isinstance(response_body, list):
                        similar_designs = response_body
                        metadata = {'total': len(response_body)}
                    elif isinstance(response_body, dict):
                        similar_designs = response_body.get('data', [])
                        metadata = response_body.get('metadata', {})
                    else:
                        logger.error(f"Invalid response body type: {type(response_body)}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Invalid response body type: {type(response_body)}"
                        )
                    
                    if not isinstance(similar_designs, list):
                        logger.error(f"Invalid similar_designs type: {type(similar_designs)}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Invalid similar_designs type: {type(similar_designs)}"
                        )
                        
                    logger.info(f"Found {len(similar_designs)} similar designs")
                    logger.info(f"Response metadata: {metadata}")
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse response body: {str(e)}")
                    logger.error(f"Raw body content: {response['body']}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to parse Lambda response: {str(e)}"
                    )
                
                # Calculate pagination
                start_idx = (page - 1) * limit
                end_idx = start_idx + limit
                paginated_designs = similar_designs[start_idx:end_idx]
                
                # Fetch design details
                design_details = await self._get_design_details(paginated_designs)
                logger.info(f"Retrieved details for {len(design_details)} designs")

                return {
                    "results": design_details,
                    "total": metadata.get('total', len(similar_designs)),
                    "page": page,
                    "total_pages": (metadata.get('total', len(similar_designs)) + limit - 1) // limit
                }

            except HTTPException as he:
                raise he
            except Exception as e:
                logger.error(f"Error processing Lambda response: {str(e)}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Error processing search results: {str(e)}"
                )

        except HTTPException as he:
            raise he
        except Exception as e:
            logger.error(f"Image search failed: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Image search failed: {str(e)}"
            )
        
        finally:
            # Cleanup
            if search_key:
                try:
                    await self.s3_handler.delete_file(
                        self.s3_handler.thumbnails_bucket,
                        search_key
                    )
                    logger.info(f"Successfully deleted search image: {search_key}")
                except Exception as e:
                    logger.warning(f"Failed to delete search image: {str(e)}")

    async def _call_lambda(self, payload: dict) -> dict:
        try:
            response = self.lambda_client.invoke(
                FunctionName=self.lambda_endpoint,
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            # Check if the response contains an error
            if 'FunctionError' in response:
                error_details = json.loads(response['Payload'].read())
                logger.error(f"Lambda function error: {error_details}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Lambda function error: {json.dumps(error_details)}"
                )
            
            return json.loads(response['Payload'].read())
        except Exception as e:
            logger.error(f"Lambda invocation failed: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Lambda invocation failed: {str(e)}"
            )

    async def _get_design_details(self, similar_designs: List[dict]) -> List[dict]:
        try:
            design_details = []
            for design in similar_designs:
                design_id = design['hash']
                response = self.dynamodb.get_item(
                    TableName=self.design_table,
                    Key={'design_id': {'S': design_id}}
                )
                
                if 'Item' in response:
                    item = response['Item']
                    # Check if design is verified
                    if item.get('verification_status', {}).get('S') != 'Verified':
                        continue
                    
                    metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                    
                    # Get seller username
                    seller_username = await get_username_from_email(
                        item['seller_email']['S'],
                        self.dynamodb
                    )
                    
                    design_details.append({
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
                    })
            logger.info(f"Fetched {len(design_details)} verified design details")
            print("These are the design details", design_details)
            return design_details
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch design details: {str(e)}") 
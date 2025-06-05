from typing import List, Optional
import boto3
import os
import json
from fastapi import HTTPException
from .dynamodb import _get_dynamodb_client
import logging
from app.utils.user import get_username_from_email

logger = logging.getLogger(__name__)

class TextSearchService:
    def __init__(self):
        logger.info("Initializing TextSearchService")
        self.lambda_endpoint = os.getenv('SEARCH_LAMBDA_ENDPOINT')
        self.design_table = os.getenv('DYNAMODB_DESIGN_TABLE')
        self.aws_region = os.getenv('AWS_REGION')
        self.dynamodb = _get_dynamodb_client()
        logger.info("TextSearchService initialized")

    async def search_similar_designs(self, query: str, page: int = 1, limit: int = 20) -> dict:
        try:
            logger.info(f"Searching for text: {query}")
            payload = {
                "httpMethod": "POST",
                "body": json.dumps({
                    "path": "/search",
                    "file_url": "",
                    "text": query,
                    "org_id": "textile-marketplace",
                    "collection_name": "image-search",
                    "userEmail": "search@system.internal"
                })
            }
            
            response = await self._call_lambda(payload)
            logger.info(f"Raw Lambda response: {response}")
            
            # Detailed response validation
            if not isinstance(response, dict):
                logger.error(f"Invalid response type: {type(response)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Invalid Lambda response type: {type(response)}"
                )
                
            if 'statusCode' not in response:
                logger.error("No statusCode in Lambda response")
                raise HTTPException(
                    status_code=500,
                    detail="Missing statusCode in Lambda response"
                )
                
            if 'body' not in response:
                logger.error("No body in Lambda response")
                raise HTTPException(
                    status_code=500,
                    detail="Missing body in Lambda response"
                )

            # Parse lambda response
            if response.get('statusCode') != 200:
                try:
                    error_body = json.loads(response.get('body', '{}'))
                    error_msg = error_body.get('error', 'Search failed')
                    logger.error(f"Lambda error response body: {error_body}")
                except json.JSONDecodeError as e:
                    error_msg = f"Failed to parse error response: {str(e)}"
                    logger.error(f"Invalid error response body: {response.get('body')}")
                
                logger.error(f"Lambda returned error: {error_msg}")
                raise HTTPException(
                    status_code=response.get('statusCode', 500),
                    detail=error_msg
                )
            print("This is the response", response)
            # Parse the response body with detailed error handling
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

        except Exception as e:
            logger.error(f"Text search failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Text search failed: {str(e)}")

    async def _call_lambda(self, payload: dict) -> dict:
        try:
            lambda_client = boto3.client('lambda',region_name=self.aws_region)
            response = lambda_client.invoke(
                FunctionName=self.lambda_endpoint,
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            return json.loads(response['Payload'].read())
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lambda invocation failed: {str(e)}")

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
                    print("These are the design details: ", design_details)
            return design_details
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch design details: {str(e)}")
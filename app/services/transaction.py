import boto3
import os
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

TRANSACTION_TABLE = os.getenv('DYNAMODB_TRANSACTION_TABLE')
DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')

class TransactionService:
    def __init__(self):
        self.dynamodb = boto3.client('dynamodb', region_name='ap-south-1')
        
    async def create_transaction(self, cart_items: list, buyer_email: str, razorpay_payment_id: str, status: str = 'COMPLETED') -> Dict[str, Any]:
        """Create a new transaction record and update design sold counts if status is COMPLETED"""
        try:
            amount = sum(float(item.get('price', 0)) for item in cart_items)
            transaction_id = str(uuid.uuid4())
            
            # Prepare designs list
            designs = []
            for item in cart_items:
                design_item = {
                    'M': {
                        'design_id': {'S': item['id']},
                        'title': {'S': item.get('title', '')},
                        'price': {'N': str(item['price'])},
                        'thumbnail_url': {'S': item.get('thumbnail_url', '')}
                    }
                }
                designs.append(design_item)
            
            # Create transaction record
            transaction_item = {
                'transaction_id': {'S': transaction_id},
                'buyer_email': {'S': buyer_email},
                'designs': {'L': designs},
                'total_amount': {'N': str(amount)},
                'status': {'S': status.upper()},
                'razorpay_payment_id': {'S': razorpay_payment_id},
                'created_at': {'S': str(datetime.now())},
                'updated_at': {'S': str(datetime.now())}
            }

            self.dynamodb.put_item(
                TableName=TRANSACTION_TABLE,
                Item=transaction_item
            )

            # If status is COMPLETED, update the sold count for each design
            if status.upper() == 'COMPLETED':
                print("Updating sold counts for designs...")
                for item in cart_items:
                    try:
                        # Increment the total_sold for each design
                        self.dynamodb.update_item(
                            TableName=DESIGN_TABLE,
                            Key={'design_id': {'S': item['id']}},
                            UpdateExpression='ADD total_sold :inc SET updated_at = :time',
                            ExpressionAttributeValues={
                                ':inc': {'N': '1'},
                                ':time': {'S': str(datetime.now())}
                            }
                        )
                        print(f"Updated sold count for design: {item['id']}")
                    except Exception as design_error:
                        print(f"Error updating sold count for design {item['id']}: {str(design_error)}")
                        continue
            
            return {
                'transaction_id': transaction_id,
                'status': status,
                'amount': amount
            }
            
        except Exception as e:
            print(f"Error creating transaction: {str(e)}")
            raise e

    async def get_transaction(self, transaction_id: str, buyer_email: str) -> Dict[str, Any]:
        """Get transaction details"""
        try:
            # Get transaction from DynamoDB
            response = self.dynamodb.get_item(
                TableName=TRANSACTION_TABLE,
                Key={
                    'transaction_id': {'S': transaction_id}
                }
            )
            
            if 'Item' not in response:
                return None
            
            item = response['Item']
            
            # Verify the buyer email matches (security check)
            if item['buyer_email']['S'] != buyer_email:
                return None
            
            # Format the response
            return {
                'transaction_id': item['transaction_id']['S'],
                'buyer_email': item['buyer_email']['S'],
                'total_amount': float(item['total_amount']['N']),
                'status': item['status']['S'],
                'created_at': item['created_at']['S'],
                'designs': [
                    {
                        'design_id': d['M']['design_id']['S'],
                        'title': d['M']['title']['S'],
                        'price': float(d['M']['price']['N']),
                        'thumbnail_url': d['M']['thumbnail_url']['S']
                    } for d in item['designs']['L']
                ]
            }
            
        except Exception as e:
            print(f"Error fetching transaction: {str(e)}")
            raise e

    async def get_user_transactions(self, buyer_email: str, page: int = 1, limit: int = 10) -> Dict[str, Any]:
        """Get user transactions with pagination"""
        try:
            # Prepare base query parameters
            query_params = {
                'TableName': TRANSACTION_TABLE,
                'IndexName': 'buyer_email-created_at-index',
                'KeyConditionExpression': 'buyer_email = :email',
                'ExpressionAttributeValues': {
                    ':email': {'S': buyer_email}
                },
                'ScanIndexForward': False,  # Sort in descending order (newest first)
                'Limit': limit
            }
            
            # Add ExclusiveStartKey only if page > 1 and we have a valid key
            if page > 1:
                last_key = await self._get_last_evaluated_key(buyer_email, page, limit)
                if last_key:
                    query_params['ExclusiveStartKey'] = last_key
            
            # Execute query
            response = self.dynamodb.query(**query_params)
            
            # Format transactions
            transactions = [self._format_transaction(item) for item in response.get('Items', [])]
            
            # Get total count
            count_response = self.dynamodb.query(
                TableName=TRANSACTION_TABLE,
                IndexName='buyer_email-created_at-index',
                KeyConditionExpression='buyer_email = :email',
                ExpressionAttributeValues={
                    ':email': {'S': buyer_email}
                },
                Select='COUNT'
            )
            
            return {
                "transactions": transactions,
                "total": count_response.get('Count', 0),
                "page": page,
                "limit": limit,
                "has_more": 'LastEvaluatedKey' in response
            }
            
        except Exception as e:
            print(f"Error fetching user transactions: {str(e)}")
            raise e

    async def _get_last_evaluated_key(self, buyer_email: str, target_page: int, limit: int) -> Optional[Dict]:
        """Helper to get the LastEvaluatedKey for pagination"""
        try:
            last_evaluated_key = None
            current_page = 1

            while current_page < target_page:
                query_params = {
                    'TableName': TRANSACTION_TABLE,
                    'IndexName': 'buyer_email-created_at-index',
                    'KeyConditionExpression': 'buyer_email = :email',
                    'ExpressionAttributeValues': {
                        ':email': {'S': buyer_email}
                    },
                    'Limit': limit
                }
                
                if last_evaluated_key:
                    query_params['ExclusiveStartKey'] = last_evaluated_key
                
                response = self.dynamodb.query(**query_params)
                last_evaluated_key = response.get('LastEvaluatedKey')
                
                if not last_evaluated_key:
                    break
                    
                current_page += 1

            return last_evaluated_key
        except Exception as e:
            print(f"Error getting last evaluated key: {str(e)}")
            return None

    def _format_transaction(self, item: Dict) -> Dict:
        """Format a DynamoDB transaction item into a regular dictionary"""
        return {
            'transaction_id': item['transaction_id']['S'],
            'buyer_email': item['buyer_email']['S'],
            'total_amount': float(item['total_amount']['N']),
            'status': item['status']['S'],
            'created_at': item['created_at']['S'],
            'updated_at': item['updated_at']['S'],
            'razorpay_payment_id': item['razorpay_payment_id']['S'],
            'designs': [
                {
                    'design_id': d['M']['design_id']['S'],
                    'title': d['M']['title']['S'],
                    'price': float(d['M']['price']['N']),
                    'thumbnail_url': d['M']['thumbnail_url']['S'],
                } for d in item['designs']['L']
            ]
        }

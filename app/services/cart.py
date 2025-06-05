import boto3
import os
from typing import List, Dict, Any

class CartService:
    def __init__(self):
        self.dynamodb = boto3.client('dynamodb', os.getenv('AWS_REGION'))
        self.table_name = os.getenv('DYNAMODB_USER_TABLE')

    async def get_cart(self, user_email: str) -> List[Dict[str, Any]]:
        """Get cart items for a user"""
        try:
            response = self.dynamodb.get_item(
                TableName=self.table_name,
                Key={'email': {'S': user_email}},
                ProjectionExpression='cart'
            )
            
            if 'Item' not in response or 'cart' not in response['Item']:
                return []
            
            # Extract cart items from DynamoDB format
            cart_items = response['Item']['cart'].get('L', [])
            return cart_items
            
        except Exception as e:
            print(f"Error fetching cart: {str(e)}")
            return []

    async def update_cart(self, user_email: str, cart_items: List[Dict[str, Any]]) -> bool:
        """Update cart items for a user"""
        try:
            # Convert the cart items to DynamoDB format
            dynamo_cart_items = []
            for item in cart_items:
                dynamo_item = {
                    'M': {
                        'id': {'S': str(item['id'])},
                        'title': {'S': str(item['title'])},
                        'price': {'N': str(item['price'])},
                        'thumbnail_url': {'S': str(item['thumbnail_url'])}
                    }
                }
                dynamo_cart_items.append(dynamo_item)

            # Update the cart in DynamoDB
            self.dynamodb.update_item(
                TableName=self.table_name,
                Key={'email': {'S': user_email}},
                UpdateExpression='SET cart = :cart',
                ExpressionAttributeValues={
                    ':cart': {'L': dynamo_cart_items}
                }
            )
            return True
        except Exception as e:
            print(f"Error updating cart: {str(e)}")
            return False 
import os
import boto3

USER_TABLE = os.getenv('DYNAMODB_USER_TABLE')

async def get_username_from_email(email: str, dynamodb) -> str:
    try:
        response = dynamodb.get_item(
            TableName=USER_TABLE,
            Key={'email': {'S': email}}
        )
        print(response)
        user = response.get('Item')
        if user:
            return user.get('username', {}).get('S', 'Anonymous')
        return 'Anonymous'
    except:
        return 'Anonymous'

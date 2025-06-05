from mangum import Mangum
from app.main import app
import json

# Create handler for AWS Lambda
handler = Mangum(app)

# Optional: Add logging for debugging
def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")  # Debug logging
    return handler(event, context)
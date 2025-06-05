from typing import Dict, Any, Tuple
from datetime import datetime
import pytz
import os

PAYMENT_HISTORY_TABLE = os.getenv('DYNAMODB_PAYMENT_HISTORY_TABLE')
DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')

class PaymentService:
    def __init__(self, dynamodb_client):
        self.dynamodb = dynamodb_client
        self.CREDIT_VALUE = 10  # 1 credit = 10 INR

    def calculate_payment_and_credits(self, price: float, payment_method: str) -> Tuple[float, int]:
        """Calculate cash payment and credits based on payment method"""
        if payment_method == 'credits_100':
            credits = int(price / self.CREDIT_VALUE)
            return 0, credits
        elif payment_method == 'hybrid_50_50':
            cash_amount = price * 0.5
            credits = int((price * 0.5) / self.CREDIT_VALUE)
            return cash_amount, credits
        elif payment_method == 'cash_100':
            # 20% commission on cash payments
            return price * 0.8, 0
        return 0, 0

    async def mark_designs_as_paid(self, email: str) -> bool:
        """Mark all sold designs for a user as paid by updating last_payout_sold"""
        try:
            # Get all designs for the user
            designs = self.dynamodb.query(
                TableName=DESIGN_TABLE,
                IndexName='DesignSellerGSI',
                KeyConditionExpression='seller_email = :email',
                ExpressionAttributeValues={
                    ':email': {'S': email}
                }
            )

            # Update each design's last_payout_sold
            for design in designs.get('Items', []):
                design_id = design['design_id']['S']
                total_sold = int(design.get('total_sold', {}).get('N', '0'))
                
                self.dynamodb.update_item(
                    TableName=DESIGN_TABLE,
                    Key={'design_id': {'S': design_id}},
                    UpdateExpression='SET last_payout_sold = :sold',
                    ExpressionAttributeValues={
                        ':sold': {'N': str(total_sold)}
                    }
                )

            return True
        except Exception as e:
            print(f"Error marking designs as paid: {str(e)}")
            return False

    def get_unpaid_sales(self, design: Dict[str, Any]) -> int:
        """Calculate unpaid sales for a design"""
        total_sold = int(design.get('total_sold', {}).get('N', '0'))
        last_payout = int(design.get('last_payout_sold', {}).get('N', '0'))
        return total_sold - last_payout 
    
    def update_payment_details(self, email: str, payment_details: Dict[str, Any]) -> bool:
        """Update payment details for a user"""
        try:
            payment_id = f"PAY_{datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y%m%d%H%M%S')}_{email.split('@')[0]}"
            payment_history = {
            'payment_id': {'S': payment_id},
            'seller_email': {'S': email},
            'total_amount': {'N': str(payment_details.get('total_amount', 0))},
            'total_credits': {'N': str(payment_details.get('total_credits', 0))},
            'transaction_id': {'S': payment_details.get('transaction_id', '')},
            'payment_date': {'S': datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()},
            'paid_designs': {'L': [{'M': {
                'category': {'S': d['category']},
                'title': {'S': d['title']},
                'sales_count': {'N': str(d['unpaid_sales'])},
                'price': {'N': str(d['price'])},
                'image_url': {'S': d['image_url']},
                'payment_method': {'S': d['payment_method']}
            }} for d in payment_details.get('paid_designs', [])]},
            'admin_email': {'S': payment_details.get('admin_email', '')},
            'notes': {'S': payment_details.get('notes', '')}
            }

            # Save payment history
            self.dynamodb.put_item(
                TableName=PAYMENT_HISTORY_TABLE,
                Item=payment_history
            )
            return True
        except Exception as e:
            print(f"Error updating payment details: {str(e)}")
            return False


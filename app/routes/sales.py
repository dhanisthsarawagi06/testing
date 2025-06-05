from fastapi import APIRouter, HTTPException, Depends, Query
from app.services import dynamodb as dynamodb_service
from app.services.auth import get_current_user
from app.services.payment import PaymentService
from typing import Optional, List, Dict, Any
from datetime import datetime
from collections import defaultdict
import logging
import pytz
import os
DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')
PAYMENT_HISTORY_TABLE = os.getenv('DYNAMODB_PAYMENT_HISTORY_TABLE')

router = APIRouter()
payment_service = PaymentService(dynamodb_service._get_dynamodb_client())
logger = logging.getLogger(__name__)

@router.get("/dashboard")
async def get_sales_dashboard(
    page: int = 1,
    limit: int = 10,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Query designs using DesignSellerGSI
        designs = dynamodb_service.query_table(
            table_name=DESIGN_TABLE,
            index_name='DesignSellerGSI',
            key_condition_expression='seller_email = :email',
            expression_attribute_values={
                ':email': {'S': current_user['email']}
            }
        )
        
        total_cash = 0
        total_credits = 0
        total_orders = 0
        processed_designs = []

        # Process each design
        for design in designs:
            if design.get('verification_status', {}).get('S') == 'Verified':
                price = float(design.get('price', {}).get('N', 0))
                total_sold = int(design.get('total_sold', {}).get('N', 0))
                unpaid_sales = payment_service.get_unpaid_sales(design)
                payment_method = design.get('payment_method', {}).get('S', '')
                
                # Calculate revenue for unpaid sales
                cash, credits = payment_service.calculate_payment_and_credits(
                    price * unpaid_sales,
                    payment_method
                )
                
                total_cash += cash
                total_credits += credits
                total_orders += unpaid_sales

                # Calculate lifetime revenue
                lifetime_cash, lifetime_credits = payment_service.calculate_payment_and_credits(
                    price * total_sold,
                    payment_method
                )
                
                processed_designs.append({
                    'id': design.get('design_id', {}).get('S', ''),
                    'title': design.get('title', {}).get('S', ''),
                    'thumbnail_url': design.get('thumbnail_url', {}).get('S', ''),
                    'category': design.get('category', {}).get('S', ''),
                    'payment_method': design.get('payment_method', {}).get('S', ''),
                    'price': price,
                    'sales': total_sold,
                    'unpaid_sales': unpaid_sales,
                    'cash_revenue': lifetime_cash,
                    'credits_earned': lifetime_credits,
                    'lifetime_revenue': lifetime_cash + (lifetime_credits * payment_service.CREDIT_VALUE)
                })

        # Sort designs by sales in descending order
        processed_designs.sort(key=lambda x: x['sales'], reverse=True)
        
        # Pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_designs = processed_designs[start_idx:end_idx]
        
        # Calculate average order value using total price
        total_price = 0
        for design in designs:
            if design.get('verification_status', {}).get('S') == 'Verified':
                price = float(design.get('price', {}).get('N', 0))
                unpaid_sales = payment_service.get_unpaid_sales(design)
                total_price += price * unpaid_sales

        # Calculate average order value using total price
        avg_order_value = total_price / total_orders if total_orders > 0 else 0
        
        return {
            "currentPeriod": {
                "revenue": total_cash,
                "credits": total_credits,
                "orders": total_orders,
                "averageOrderValue": avg_order_value
            },
            "topDesigns": paginated_designs,
            "pagination": {
                "total": len(processed_designs),
                "pages": (len(processed_designs) + limit - 1) // limit,
                "current": page
            }
        }

    except Exception as e:
        print(f"Error fetching sales dashboard: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/analytics")
async def get_sales_analytics(
    year: int = Query(..., description="Year for analytics"),
    current_user: dict = Depends(get_current_user)
):
    try:
        seller_email = current_user['email']
        
        # Get all payment history for the user in the specified year
        response = dynamodb_service.query(
            table_name=PAYMENT_HISTORY_TABLE,
            key_condition_expression='#seller_email = :email AND begins_with(#payment_date, :year)',
            expression_attribute_names={'#seller_email': 'seller_email', '#payment_date': 'payment_date'},
            expression_attribute_values={
                ':email': {'S': seller_email},
                ':year': {'S': str(year)}
            }
        )

        # Initialize monthly revenue array with zeros
        monthly_revenue = [0] * 12
        monthly_credits = [0] * 12
        category_revenue = {}
        
        # Process each payment record
        for payment in response:
            # Extract payment date and parse it
            payment_date = datetime.fromisoformat(payment['payment_date']['S'])
            month_index = payment_date.month - 1  # 0-based index for months

            # Add total amount to monthly revenue
            total_amount = float(payment['total_amount']['N'])
            monthly_revenue[month_index] += total_amount

            # Add total credits to monthly credits
            total_credits = int(payment['total_credits']['N'])
            monthly_credits[month_index] += total_credits

            # Process category-wise revenue
            for design in payment['paid_designs']['L']:
                design_data = design['M']
                category = design_data.get('category', {'S': 'Uncategorized'})['S']
                price = float(design_data['price']['N'])
                sales_count = int(design_data['sales_count']['N'])
                revenue = price * sales_count

                if category in category_revenue:
                    category_revenue[category] += revenue
                else:
                    category_revenue[category] = revenue

        # Get available years (for year selector)
        available_years = await get_available_years(seller_email)

        # Format category revenue for pie chart
        category_data = [
            {"name": category, "value": revenue}
            for category, revenue in category_revenue.items()
        ]

        return {
            "monthly_revenue": monthly_revenue,
            "monthly_credits": monthly_credits,
            "category_revenue": category_data,
            "available_years": available_years
        }

    except Exception as e:
        logger.error(f"Error fetching sales analytics: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching sales analytics: {str(e)}"
        )

async def get_available_years(seller_email: str) -> List[int]:
    """Get list of years for which payment data exists"""
    try:
        # Query all payment records for the seller
        response = dynamodb_service.query(
            table_name=PAYMENT_HISTORY_TABLE,
            key_condition_expression='#seller_email = :email',
            expression_attribute_names={'#seller_email': 'seller_email'},
            expression_attribute_values={
                ':email': {'S': seller_email}
            }
        )

        # Extract years from payment dates
        years = set()
        for item in response:
            payment_date = datetime.fromisoformat(item['payment_date']['S'])
            years.add(payment_date.year)

        # Return sorted list of years
        return sorted(list(years), reverse=True)

    except Exception as e:
        logger.error(f"Error fetching available years: {str(e)}")
        return [datetime.now(pytz.timezone('Asia/Kolkata')).year] 
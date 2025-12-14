import boto3
import logging
from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class CostManager:
    def __init__(self):
        """
        Initializes the Cost Explorer client.
        """
        self.client = boto3.client('ce', region_name='us-east-1')

    def get_total_cost_for_group(self, group_tag_value: str, start_date: str, end_date: Optional[str] = None) -> float:
        """
        Retrieves the total unblended cost for a specific IAM Group (via Tag).
        Uses DAILY granularity for precise calculation.
        Handles date logic:
         - Adds +1 day to end_date (inclusive logic).
         - Returns 0.0 if start_date is in the future.
        """
        today = datetime.now(timezone.utc).date()

        try:
            start_dt_obj = datetime.strptime(start_date, '%Y-%m-%d').date()

            if end_date:
                end_dt_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            else:
                end_dt_obj = today

        except ValueError as e:
            logger.error(f"❌ Date format error (expected YYYY-MM-DD): {e}")
            return 0.0

        if start_dt_obj > today:
            logger.warning(f"⚠️ Start date {start_date} is in the future. Returning 0.0.")
            return 0.0

        adjusted_end_dt = end_dt_obj + timedelta(days=1)
        aws_end_date = adjusted_end_dt.strftime('%Y-%m-%d')

        logger.info(f"💰 Fetching total cost for Group Tag='{group_tag_value}' "
                    f"(Range: {start_date} to {end_date}, AWS Query End: {aws_end_date})")

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': aws_end_date},
                Granularity='DAILY',
                Metrics=['UnblendedCost'],
                Filter={
                    'Tags': {
                        'Key': 'Group',
                        'Values': [group_tag_value]
                    }
                }
            )

            total = 0.0
            for result in response.get('ResultsByTime', []):
                amount = result.get('Total', {}).get('UnblendedCost', {}).get('Amount')
                if amount:
                    total += float(amount)

            return round(total, 2)

        except ClientError as error:
            logger.error(f"❌ AWS Error fetching costs for group '{group_tag_value}': {error}")
            return 0.0

    def get_group_cost_with_service_breakdown(self, group_tag_value: str, start_date: str,
                                              end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves cost breakdown by AWS Service for a specific group.
        Handles date logic:
         - Adds +1 day to end_date (inclusive logic).
         - Returns empty structure if start_date is in the future.
        """
        # 1. Prepare Date Objects
        today = datetime.now(timezone.utc).date()

        try:
            start_dt_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            if end_date:
                end_dt_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            else:
                end_dt_obj = today
        except ValueError as e:
            logger.error(f"❌ Date format error: {e}")
            return {'total': 0.0, 'by_service': {}}

        # 2. FUTURE GUARD: If start date is in the future, return empty.
        if start_dt_obj > today:
            logger.warning(f"⚠️ Start date {start_date} is in the future. Returning empty breakdown.")
            return {'total': 0.0, 'by_service': {}}

        # 3. LOGIC FIX: AWS Exclusive End Date (+1 Day)
        adjusted_end_dt = end_dt_obj + timedelta(days=1)
        aws_end_date = adjusted_end_dt.strftime('%Y-%m-%d')

        logger.info(f"🔍 Fetching service breakdown for Group Tag='{group_tag_value}' "
                    f"(Range: {start_date} to {end_date}, AWS Query End: {aws_end_date})")

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': aws_end_date},
                Granularity='MONTHLY',  # Monthly is fine for service aggregations
                Metrics=['UnblendedCost'],
                Filter={
                    'Tags': {
                        'Key': 'Group',
                        'Values': [group_tag_value]
                    }
                },
                GroupBy=[
                    {'Type': 'DIMENSION', 'Key': 'SERVICE'}
                ]
            )

            cost_by_service = {}
            total_cost = 0.0

            for result_by_time in response['ResultsByTime']:
                for group in result_by_time['Groups']:
                    service_name = group['Keys'][0]
                    amount = float(group['Metrics']['UnblendedCost']['Amount'])

                    if amount <= 0:
                        continue

                    cost_by_service[service_name] = cost_by_service.get(service_name, 0.0) + amount
                    total_cost += amount

            sorted_services = {k: round(v, 2) for k, v in sorted(
                cost_by_service.items(),
                key=lambda item: item[1],
                reverse=True
            )}

            return {
                'total': round(total_cost, 2),
                'by_service': sorted_services
            }

        except ClientError as error:
            logger.error(f"❌ AWS Error fetching breakdown for group '{group_tag_value}': {error}")
            return {'total': 0.0, 'by_service': {}}

    def get_total_costs_for_all_groups(self, start_date: str, end_date: Optional[str] = None) -> Dict[str, float]:
        """
        Retrieves total costs grouped by the 'Group' tag for all groups.
        Handles date logic (inclusive end date, future guard).
        """
        # 1. Prepare Date Objects
        today = datetime.now(timezone.utc).date()

        try:
            start_dt_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            if end_date:
                end_dt_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            else:
                end_dt_obj = today
        except ValueError as e:
            logger.error(f"❌ Date format error: {e}")
            return {}

        # 2. FUTURE GUARD
        if start_dt_obj > today:
            logger.warning(f"⚠️ Start date {start_date} is in the future. Returning empty list.")
            return {}

        # 3. LOGIC FIX: AWS Exclusive End Date (+1 Day)
        adjusted_end_dt = end_dt_obj + timedelta(days=1)
        aws_end_date = adjusted_end_dt.strftime('%Y-%m-%d')

        logger.info(f"📊 Fetching costs for ALL groups ({start_date} to {end_date}, AWS End: {aws_end_date})")
        group_costs = {}

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': aws_end_date},
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                GroupBy=[{'Type': 'TAG', 'Key': 'Group'}]
            )

            for result in response['ResultsByTime']:
                for group in result['Groups']:
                    tag_key = group['Keys'][0]

                    if '$' in tag_key:
                        group_name = tag_key.split('$', 1)[1]
                    else:
                        group_name = tag_key

                    if not group_name:
                        continue

                    cost = float(group['Metrics']['UnblendedCost']['Amount'])

                    group_costs[group_name] = group_costs.get(group_name, 0.0) + cost

            return {group: round(cost, 2) for group, cost in group_costs.items()}

        except ClientError as error:
            logger.error(f"❌ AWS Error fetching all group costs: {error}")
            return {}

    def get_total_aws_cost(self, start_date: str, end_date: Optional[str] = None) -> float:
        """
        Retrieves the global AWS cost for the account (no filters).
        Handles date logic (inclusive end date, future guard).
        """
        # 1. Prepare Date Objects
        today = datetime.now(timezone.utc).date()

        try:
            start_dt_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            if end_date:
                end_dt_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            else:
                end_dt_obj = today
        except ValueError as e:
            logger.error(f"❌ Date format error: {e}")
            return 0.0

        # 2. FUTURE GUARD
        if start_dt_obj > today:
            logger.warning(f"⚠️ Start date {start_date} is in the future. Returning 0.0.")
            return 0.0

        # 3. LOGIC FIX: AWS Exclusive End Date (+1 Day)
        adjusted_end_dt = end_dt_obj + timedelta(days=1)
        aws_end_date = adjusted_end_dt.strftime('%Y-%m-%d')

        logger.info(f"🌐 Fetching GLOBAL AWS costs ({start_date} to {end_date}, AWS End: {aws_end_date})")

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': aws_end_date},
                Granularity='DAILY',  # DAILY is safer for arbitrary date ranges
                Metrics=['UnblendedCost']
                # No Filter = Global Cost
            )

            total = 0.0
            for result in response.get('ResultsByTime', []):
                amount = result.get('Total', {}).get('UnblendedCost', {}).get('Amount')
                if amount:
                    total += float(amount)

            return round(total, 2)

        except ClientError as error:
            logger.error(f"❌ AWS Error fetching global costs: {error}")
            return 0.0

    def get_total_cost_with_service_breakdown(self, start_date: str, end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves GLOBAL AWS costs broken down by service.
        Handles date logic (inclusive end date, future guard).
        """
        # 1. Prepare Date Objects
        today = datetime.now(timezone.utc).date()

        try:
            start_dt_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            if end_date:
                end_dt_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            else:
                end_dt_obj = today
        except ValueError as e:
            logger.error(f"❌ Date format error: {e}")
            return {'total': 0.0, 'by_service': {}}

        # 2. FUTURE GUARD
        if start_dt_obj > today:
            logger.warning(f"⚠️ Start date {start_date} is in the future. Returning empty breakdown.")
            return {'total': 0.0, 'by_service': {}}

        # 3. LOGIC FIX: AWS Exclusive End Date (+1 Day)
        adjusted_end_dt = end_dt_obj + timedelta(days=1)
        aws_end_date = adjusted_end_dt.strftime('%Y-%m-%d')

        logger.info(f"🧾 Fetching GLOBAL AWS cost breakdown ({start_date} to {end_date}, AWS End: {aws_end_date})")

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': aws_end_date},
                Granularity='MONTHLY',  # Monthly is fine for high-level aggregations
                Metrics=['UnblendedCost'],
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
            )

            if not response.get('ResultsByTime'):
                return {'total': 0.0, 'by_service': {}}

            total_cost = 0.0
            cost_by_service = {}

            for result_by_time in response['ResultsByTime']:
                # A. Aggregate Total Cost for this time chunk
                total_chunk = result_by_time.get('Total', {}).get('UnblendedCost', {}).get('Amount')
                if total_chunk:
                    try:
                        total_cost += float(total_chunk)
                    except ValueError:
                        pass

                # B. Aggregate Service Costs
                for group in result_by_time.get('Groups', []):
                    service_name = group.get('Keys', [None])[0]
                    if not service_name: continue

                    amount_str = group.get('Metrics', {}).get('UnblendedCost', {}).get('Amount')
                    if amount_str:
                        try:
                            amount = float(amount_str)
                            if amount > 0:
                                cost_by_service[service_name] = cost_by_service.get(service_name, 0.0) + amount
                        except ValueError:
                            continue

            # Sort by cost descending (highest first)
            sorted_services = {k: round(v, 2) for k, v in sorted(
                cost_by_service.items(), key=lambda item: item[1], reverse=True
            )}

            return {
                'total': round(total_cost, 2),
                'by_service': sorted_services
            }

        except ClientError as e:
            logger.error(f"❌ AWS Error fetching global breakdown: {e}")
            return {'total': 0.0, 'by_service': {}}

    def get_group_cost_last_6_months_by_service(self, group_tag_value: str) -> Dict[str, float]:
        """
        Returns accumulated costs for the last 6 months per service, mapped to short names.
        """
        start_date, end_date = self._get_last_6_months_window()

        logger.info(f"Fetching 6-month service history for '{group_tag_value}'")

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                Filter={
                    'Tags': {
                        'Key': 'Group',
                        'Values': [group_tag_value]
                    }
                },
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
            )

            costs = {}
            for by_time in response.get('ResultsByTime', []):
                for grp in by_time.get('Groups', []):
                    aws_name = grp.get('Keys', [''])[0]
                    amount = float(grp.get('Metrics', {}).get('UnblendedCost', {}).get('Amount', '0'))
                    if amount <= 0:
                        continue

                    short = self._aws_service_to_short(aws_name)
                    costs[short] = round(costs.get(short, 0.0) + amount, 10)

            return {k: round(v, 2) for k, v in costs.items()}

        except ClientError as error:
            logger.error(f"AWS Error fetching 6-month history: {error}")
            return {}

    def get_group_monthly_costs_last_6_months(self, group_tag_value: str) -> Dict[str, float]:
        """
        Returns total costs per month for the last 6 months.
        Ensures all 6 months are present in the result (even if cost is 0.0).
        Format: {'dd-mm-yyyy': cost}
        """
        # 1. Calculate the list of expected months (for chart X-axis consistency)
        now = datetime.now(timezone.utc)
        month_start = self._first_day_of_month(now)
        # Start 5 months back to get a 6-month window including current
        start_dt = self._first_day_of_month(self._shift_months(month_start, -5))

        # Prepare zero-filled dict to ensure no months are missing in the chart
        # We iterate 0 to 5 (6 months)
        months_keys = []
        for i in range(0, 6):
            m_dt = self._shift_months(start_dt, i)
            # Frontend expects dd-mm-yyyy
            months_keys.append(m_dt.strftime('%d-%m-%Y'))

        # Create map: {'01-07-2025': 0.0, '01-08-2025': 0.0, ...}
        month_costs = {k: 0.0 for k in months_keys}

        # 2. Get AWS Query Dates
        start_date_str, end_date_str = self._get_last_6_months_window()

        logger.info(f"📅 Fetching 6-month monthly trend for '{group_tag_value}' "
                    f"({start_date_str} to {end_date_str})")

        try:
            response = self.client.get_cost_and_usage(
                TimePeriod={'Start': start_date_str, 'End': end_date_str},
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                Filter={
                    'Tags': {
                        'Key': 'Group',
                        'Values': [group_tag_value]
                    }
                }
            )

            # 3. Map AWS results to our pre-filled dictionary
            for by_time in response.get('ResultsByTime', []):
                period_start = by_time.get('TimePeriod', {}).get('Start')  # AWS format: 'YYYY-MM-DD'

                try:
                    # Convert YYYY-MM-DD -> dd-mm-yyyy to match our keys
                    dt_object = datetime.strptime(period_start, '%Y-%m-%d')
                    key = dt_object.strftime('%d-%m-%Y')
                except ValueError:
                    logger.warning(f"Skipping invalid date format from AWS: {period_start}")
                    continue

                amount_str = by_time.get('Total', {}).get('UnblendedCost', {}).get('Amount')
                if amount_str:
                    try:
                        amount = float(amount_str)
                        # Only update if this month is in our expected window
                        if key in month_costs:
                            month_costs[key] = round(amount, 2)
                    except ValueError:
                        pass

            return month_costs

        except ClientError as error:
            logger.error(f"❌ AWS Error fetching monthly trend: {error}")
            # Return the zero-filled dict so the frontend draws a flat line instead of crashing
            return month_costs

    # ==========================
    # Private Helpers
    # ==========================

    def _get_last_6_months_window(self):
        """Calculates start/end strings for the last 6 months window."""
        now = datetime.now(timezone.utc)
        month_start = self._first_day_of_month(now)
        # 6 months back
        start_dt = self._first_day_of_month(self._shift_months(month_start, -5))
        # 1 month forward (exclusive end)
        end_dt = self._first_day_of_month(self._shift_months(month_start, 1))

        return start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d')

    @staticmethod
    def _first_day_of_month(dt: datetime) -> datetime:
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _shift_months(dt: datetime, months: int) -> datetime:
        year = dt.year + (dt.month - 1 + months) // 12
        month = (dt.month - 1 + months) % 12 + 1
        day = min(dt.day, 28)
        return dt.replace(year=year, month=month, day=day)

    @staticmethod
    def _aws_service_to_short(name: str) -> str:
        """Normalizes long AWS service names to short codes for the frontend."""
        n = (name or '').lower()
        if 'ec2' in n or 'elastic compute cloud' in n: return 'ec2'
        if 'simple storage service' in n or 'amazon s3' in n or ' s3' in n: return 's3'
        if 'elastic block store' in n or 'ebs' in n: return 'ebs'
        if 'relational database service' in n or ' rds' in n: return 'rds'
        if 'cloudwatch' in n: return 'cloudwatch'
        if 'lambda' in n: return 'lambda'
        if 'elastic container registry' in n or ' ecr' in n: return 'ecr'
        if 'elastic kubernetes service' in n or ' eks' in n: return 'eks'
        if 'virtual private cloud' in n or ' vpc' in n: return 'vpc'
        if 'systems manager' in n or ' ssm' in n: return 'ssm'
        if 'key management service' in n or ' kms' in n: return 'kms'
        if 'dynamodb' in n: return 'dynamodb'
        if 'simple queue service' in n or ' sqs' in n: return 'sqs'
        if 'simple notification service' in n or ' sns' in n: return 'sns'
        if 'cloudtrail' in n: return 'cloudtrail'

        # Fallback heuristic
        for token in ['aurora', 'redshift', 'athena', 'glue', 'emr', 'opensearch', 'kinesis']:
            if token in n: return token

        return n.split(' - ')[0].split()[-1].replace('amazon', '').strip() or 'other'
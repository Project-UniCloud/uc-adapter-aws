import boto3
from datetime import datetime

# Create a Cost Explorer client
client = boto3.client('ce')  # Cost Explorer

def get_total_cost_for_group(group_tag_value: str, start_date: str):
    # Get the current date in UTC as the end date
    end = datetime.now(datetime.UTC).date()

    # Query AWS Cost Explorer for costs grouped by the 'Group' tag
    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end.strftime('%Y-%m-%d'),
        },
        Granularity='MONTHLY',
        Metrics=['UnblendedCost'],
        GroupBy=[
            {
                'Type': 'TAG',
                'Key': 'Group'
            }
        ]
    )

    total = 0.0
    # Iterate over each time period in the response
    for result_by_time in response['ResultsByTime']:
        # Iterate over each group in the time period
        for group in result_by_time['Groups']:
            tag_key = group['Keys'][0]
            amount = float(group['Metrics']['UnblendedCost']['Amount'])

            # Check if the group tag matches the provided value
            if f"Group${group_tag_value}" == tag_key:
                total += amount

    # Return the total cost rounded to two decimal places
    return round(total, 2)


def get_total_costs_for_all_groups(start_date: str) -> dict:
    # Get the current date in UTC as the end date
    end = datetime.now(datetime.UTC).date()

    # Query AWS Cost Explorer for costs grouped by the 'Group' tag
    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end.strftime('%Y-%m-%d'),
        },
        Granularity='MONTHLY',
        Metrics=['UnblendedCost'],
        GroupBy=[
            {
                'Type': 'TAG',
                'Key': 'Group'
            }
        ]
    )

    group_costs = {}

    # Iterate over time periods
    for result_by_time in response['ResultsByTime']:
        for group in result_by_time['Groups']:
            tag_key = group['Keys'][0]  # e.g., 'Group$GRUPA_1'
            amount = float(group['Metrics']['UnblendedCost']['Amount'])

            # Extract only the tag value (e.g., 'GRUPA_1' from 'Group$GRUPA_1')
            if '$' in tag_key:
                group_name = tag_key.split('$')[1]
            else:
                group_name = tag_key  # fallback

            # Sum cost per group
            if group_name not in group_costs:
                group_costs[group_name] = 0.0

            group_costs[group_name] += amount

    # Round the values
    return {group: round(cost, 2) for group, cost in group_costs.items()}


def get_total_aws_cost(start_date: str, end_date: str = None) -> float:
    # If end_date is not provided, use the current date in UTC
    if end_date is None:
        end_date = datetime.now(datetime.UTC).strftime('%Y-%m-%d')

    # Query AWS Cost Explorer for total costs
    response = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end_date
        },
        Granularity='MONTHLY',
        Metrics=['UnblendedCost']
    )

    total_cost = 0.0

    # Iterate through time periods (e.g., months)
    for result_by_time in response['ResultsByTime']:
        amount = float(result_by_time['Total']['UnblendedCost']['Amount'])
        total_cost += amount

    # Return the total cost rounded to two decimal places
    return round(total_cost, 2)
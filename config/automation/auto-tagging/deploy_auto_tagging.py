import boto3
import json
import time
import zipfile
import io
from botocore.exceptions import ClientError


class AutoTaggingDeployer:
    def __init__(self, region='eu-central-1'):
        self.session = boto3.Session(region_name=region)
        self.iam = self.session.client('iam')
        self.lambda_client = self.session.client('lambda')
        self.events = self.session.client('events')
        self.sts = self.session.client('sts')
        self.ec2 = self.session.client('ec2')
        self.s3 = self.session.client('s3')

        # Konfiguracja
        self.role_name = 'AutoTaggingLambdaRole'
        self.function_name = 'AutoTaggingFunction'
        self.rule_name = 'AutoTaggingRule'
        self.log_group_name = f'/aws/lambda/{self.function_name}'

    def _cleanup_existing_resources(self):
        """Usuwa istniejące zasoby przed ponownym wdrożeniem"""
        print("Cleaning up existing resources...")

        # 1. Usuń regułę EventBridge
        try:
            targets = self.events.list_targets_by_rule(Rule=self.rule_name)
            if targets['Targets']:
                self.events.remove_targets(
                    Rule=self.rule_name,
                    Ids=[target['Id'] for target in targets['Targets']]
                )
            self.events.delete_rule(Name=self.rule_name)
            print(f"Deleted EventBridge rule: {self.rule_name}")
        except self.events.exceptions.ResourceNotFoundException:
            pass
        except ClientError as e:
            print(f"Error deleting EventBridge rule: {e}")

        # 2. Usuń funkcję Lambda
        try:
            self.lambda_client.delete_function(FunctionName=self.function_name)
            print(f"Deleted Lambda function: {self.function_name}")
        except self.lambda_client.exceptions.ResourceNotFoundException:
            pass
        except ClientError as e:
            print(f"Error deleting Lambda function: {e}")

        # 3. Usuń grupę logów
        try:
            logs = self.session.client('logs')
            logs.delete_log_group(logGroupName=self.log_group_name)
            print(f"Deleted log group: {self.log_group_name}")
        except logs.exceptions.ResourceNotFoundException:
            pass
        except ClientError as e:
            print(f"Error deleting log group: {e}")

        # 4. Usuń rolę IAM (zachowaj na końcu)
        try:
            # Najpierw usuń inline policies
            policies = self.iam.list_role_policies(RoleName=self.role_name)
            for policy_name in policies['PolicyNames']:
                self.iam.delete_role_policy(
                    RoleName=self.role_name,
                    PolicyName=policy_name
                )

            # Następnie odłącz managed policies
            attached = self.iam.list_attached_role_policies(RoleName=self.role_name)
            for policy in attached['AttachedPolicies']:
                self.iam.detach_role_policy(
                    RoleName=self.role_name,
                    PolicyArn=policy['PolicyArn']
                )

            # Na końcu usuń rolę
            self.iam.delete_role(RoleName=self.role_name)
            print(f"Deleted IAM role: {self.role_name}")
        except self.iam.exceptions.NoSuchEntityException:
            pass
        except ClientError as e:
            print(f"Error deleting IAM role: {e}")

        # Poczekaj na pełne usunięcie zasobów
        time.sleep(10)

    def _create_lambda_zip(self):
        """Tworzy plik ZIP z kodem Lambda"""
        with open('lambda_function.py', 'r') as f:
            lambda_code = f.read()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zipped:
            zipped.writestr('lambda_function.py', lambda_code)
        buffer.seek(0)
        return buffer.read()

    def _wait_for_iam_propagation(self, seconds=10):
        """Czekaj na propagację zmian IAM"""
        print(f"Waiting {seconds} seconds for IAM propagation...")
        time.sleep(seconds)

    def create_iam_role(self):
        """Tworzy rolę IAM dla funkcji Lambda"""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": [
                            "lambda.amazonaws.com",
                            "events.amazonaws.com"
                        ]
                    },
                    "Action": "sts:AssumeRole"
                }
            ]
        }

        print(f"Creating IAM role {self.role_name}...")
        role = self.iam.create_role(
            RoleName=self.role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description='Role for auto-tagging Lambda function',
            Tags=[{'Key': 'auto-tagging', 'Value': 'true'}]
        )
        role_arn = role['Role']['Arn']

        self._wait_for_iam_propagation()

        # Dołącz managed policies
        self.iam.attach_role_policy(
            RoleName=self.role_name,
            PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
        )

        # Dodaj custom policy
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:CreateTags",
                        "iam:ListUserTags",
                        "iam:ListRoleTags",
                        "iam:GetUser",
                        "iam:GetRole",
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "sts:GetCallerIdentity"
                    ],
                    "Resource": "*"
                }
            ]
        }

        self.iam.put_role_policy(
            RoleName=self.role_name,
            PolicyName='AutoTaggingPermissions',
            PolicyDocument=json.dumps(policy_document)
        )

        self._wait_for_iam_propagation()
        return role_arn

    def deploy_lambda_function(self, role_arn):
        """Wdraża funkcję Lambda"""
        print(f"Deploying Lambda function {self.function_name}...")

        response = self.lambda_client.create_function(
            FunctionName=self.function_name,
            Runtime='python3.9',
            Role=role_arn,
            Handler='lambda_function.lambda_handler',
            Code={'ZipFile': self._create_lambda_zip()},
            Timeout=60,
            MemorySize=256,
            Publish=True,
            Environment={
                'Variables': {
                    'LOG_LEVEL': 'INFO'
                }
            },
            Tags={
                'auto-tagging': 'true',
                'environment': 'production'
            }
        )

        self._wait_for_iam_propagation(5)
        return response['FunctionArn']

    def setup_eventbridge_rule(self, lambda_arn):
        """Konfiguruje regułę EventBridge"""
        account_id = self.sts.get_caller_identity()['Account']

        print(f"Creating EventBridge rule {self.rule_name}...")

        # Poprawiony wzorzec zdarzeń
        event_pattern = {
            "source": ["aws.ec2"],
            "detail-type": ["AWS API Call via CloudTrail"],
            "detail": {
                "eventSource": ["ec2.amazonaws.com"],
                "eventName": ["RunInstances"]
            }
        }

        # Utwórz regułę
        self.events.put_rule(
            Name=self.rule_name,
            EventPattern=json.dumps(event_pattern),
            State='ENABLED',
            Description='Triggers auto-tagging of new EC2 instances'
        )

        # Dodaj target (uproszczony)
        self.events.put_targets(
            Rule=self.rule_name,
            Targets=[{
                'Id': '1',
                'Arn': lambda_arn
            }]
        )

        # Nadaj uprawnienia
        try:
            self.lambda_client.add_permission(
                FunctionName=self.function_name,
                StatementId='EventBridgeInvokePermission',
                Action='lambda:InvokeFunction',
                Principal='events.amazonaws.com',
                SourceArn=f'arn:aws:events:{self.session.region_name}:{account_id}:rule/{self.rule_name}'
            )
        except ClientError as e:
            if e.response['Error']['Code'] != 'ResourceConflictException':
                raise

    def deploy(self):
        """Główna metoda wdrażająca całe rozwiązanie"""
        try:
            # 1. Wyczyść istniejące zasoby
            self._cleanup_existing_resources()

            # 2. Utwórz rolę IAM
            role_arn = self.create_iam_role()
            print(f"IAM Role ARN: {role_arn}")

            # 3. Wdróż funkcję Lambda
            lambda_arn = self.deploy_lambda_function(role_arn)
            print(f"Lambda Function ARN: {lambda_arn}")

            # 4. Skonfiguruj EventBridge
            self.setup_eventbridge_rule(lambda_arn)

            print("\n=== Deployment completed successfully ===")
            print(f"Lambda Function: {lambda_arn}")
            print(f"EventBridge Rule: {self.rule_name}")
            print(f"IAM Role: {role_arn}")

        except Exception as e:
            print(f"\n!!! Deployment failed: {str(e)}")
            raise


if __name__ == '__main__':
    deployer = AutoTaggingDeployer(region='eu-central-1')
    deployer.deploy()
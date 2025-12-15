import boto3
import logging
import sys
import os
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError

current_dir = os.path.dirname(os.path.abspath(__file__))
deployer_path = os.path.abspath(os.path.join(current_dir, 'automation', 'auto-tagging'))

if deployer_path not in sys.path:
    sys.path.append(deployer_path)

try:
    from deploy_auto_tagging import AutoTaggingDeployer

    DEPLOYER_AVAILABLE = True
except ImportError:
    DEPLOYER_AVAILABLE = False

logger = logging.getLogger(__name__)


class SystemHealthCheck:
    def __init__(self, region_name='us-east-1'):
        self.region = region_name
        self.aws_available = False

        # Path to policy JSON files
        self.policies_dir = os.path.join(current_dir, 'policies')

        # Initialize AWS Clients
        try:
            self.session = boto3.Session(region_name=region_name)
            self.sts = self.session.client('sts')
            self.iam = self.session.client('iam')
            self.lambda_client = self.session.client('lambda')
            self.events_client = self.session.client('events')
            self.ce_client = self.session.client('ce')
            self.aws_available = True
        except Exception as e:
            logger.error(f"⚠️ Failed to initialize AWS clients: {e}")

        # Resource names (Must match deploy_auto_tagging.py)
        self.lambda_name = "AutoTaggingFunction"
        self.event_rule_name = "AutoTaggingMultiServiceRule"
        self.required_tag = "Group"

        # Required local configuration files
        self.required_policy_files = [
            "change_password_policy.json",
            "regional_restriction_policy.json"
        ]

    def ensure_system_integrity(self) -> bool:
        """
        Main entry point for diagnostics.
        Returns True if the system is ready (or successfully repaired).
        Returns False if the system must run in OFFLINE mode.
        """
        logger.info("🏥 STARTING SYSTEM HEALTH CHECK...")

        # 1. Check Local Files (Critical)
        if not self._check_local_files():
            logger.error("❌ CRITICAL ERROR: Missing configuration policy files.")
            return False

            # 2. Check AWS Connectivity
        if not self.aws_available or not self._check_aws_connectivity():
            logger.warning("🟠 AWS UNREACHABLE. Switching to OFFLINE mode.")
            return False

        # --- ONLINE CHECKS & REMEDIATION ---

        # 3. Configure Account Password Policy (Critical for students)
        self._ensure_account_password_policy()

        # 4. Check IAM Quotas (Informational)
        self._check_iam_quotas()

        # 5. Cost Allocation Tags (Remediation)
        self._ensure_cost_tags()

        # 6. Auto-Tagging Infrastructure (Remediation)
        infra_ok = self._check_infrastructure_paranoid()

        if not infra_ok:
            logger.warning("⚠️ Auto-Tagging infrastructure is missing or broken.")

            if DEPLOYER_AVAILABLE:
                logger.info("🔄 Starting AUTO-REMEDIATION (Redeployment)...")
                if self._run_auto_deployment():
                    logger.info("✅ Remediation successful. Infrastructure is ready.")
                    return True
                else:
                    logger.error("❌ Remediation failed. Costs may not be tracked correctly.")
                    # Return True to allow app usage, despite broken tagging
                    return True
            else:
                logger.error("❌ 'AutoTaggingDeployer' module not found. Cannot repair.")
                return True

        logger.info("🚀 SYSTEM READY. AWS environment fully configured.")
        return True

    # =========================================================================
    #                               HELPER METHODS
    # =========================================================================

    def _check_local_files(self):
        """Verifies that the policy directory and files exist."""
        if not os.path.exists(self.policies_dir):
            logger.error(f"   ❌ Policy directory not found: {self.policies_dir}")
            return False

        all_exist = True
        for fname in self.required_policy_files:
            fpath = os.path.join(self.policies_dir, fname)
            if not os.path.isfile(fpath):
                logger.error(f"   ❌ Missing file: {fname}")
                all_exist = False
        return all_exist

    def _check_aws_connectivity(self):
        try:
            self.sts.get_caller_identity()
            return True
        except (NoCredentialsError, PartialCredentialsError, ClientError):
            return False

    def _ensure_account_password_policy(self):
        """Ensures students have permission to change their own passwords."""
        try:
            self.iam.update_account_password_policy(
                MinimumPasswordLength=8,
                RequireSymbols=False,
                RequireNumbers=True,
                RequireUppercase=True,
                RequireLowercase=True,
                AllowUsersToChangePassword=True,  # Key setting
                HardExpiry=False
            )
        except ClientError:
            pass

    def _check_iam_quotas(self):
        try:
            summary = self.iam.get_account_summary()
            users = summary['SummaryMap'].get('Users', 0)
            if users > 40:
                logger.warning(f"   ⚠️ High IAM User count: {users}/50 (default limit).")
        except Exception:
            pass

    def _ensure_cost_tags(self):
        """Activates the 'Group' tag for cost allocation if inactive."""
        try:
            response = self.ce_client.list_cost_allocation_tags(
                TagKeys=[self.required_tag], Type='UserDefined', MaxResults=10
            )
            tag_data = next((t for t in response.get('CostAllocationTags', [])
                             if t['TagKey'] == self.required_tag), None)

            if tag_data and tag_data['Status'] != 'Active':
                logger.info(f"   🔄 Activating cost tag '{self.required_tag}'...")
                self.ce_client.update_cost_allocation_tags_status(
                    CostAllocationTagsStatus=[{'TagKey': self.required_tag, 'Status': 'Active'}]
                )
        except Exception:
            pass

    def _check_infrastructure_paranoid(self):
        """
        Deep check:
        1. Does Lambda exist?
        2. Is EventBridge Rule ENABLED?
        3. Does the Rule actually target the Lambda?
        """
        try:
            # 1. Check Lambda
            lambda_res = self.lambda_client.get_function(FunctionName=self.lambda_name)
            lambda_arn = lambda_res['Configuration']['FunctionArn']

            # 2. Check Rule
            rule = self.events_client.describe_rule(Name=self.event_rule_name)
            if rule['State'] != 'ENABLED':
                logger.warning("   ⚠️ EventBridge Rule was disabled. Enabling...")
                self.events_client.enable_rule(Name=self.event_rule_name)

            # 3. Check Targets (Link between Rule and Lambda)
            targets_res = self.events_client.list_targets_by_rule(Rule=self.event_rule_name)
            targets = targets_res.get('Targets', [])

            if not targets:
                logger.error("   ❌ EventBridge Rule has no targets.")
                return False

            # Check if Lambda ARN matches target ARN (using 'in' to handle version suffixes)
            is_linked = any(lambda_arn in t['Arn'] or t['Arn'] in lambda_arn for t in targets)

            if not is_linked:
                logger.error("   ❌ EventBridge Rule does not target the correct Lambda.")
                return False

            return True

        except ClientError:
            # Any missing resource triggers a rebuild
            return False

    def _run_auto_deployment(self):
        """Runs the external deployment script."""
        original_cwd = os.getcwd()
        try:
            os.chdir(deployer_path)
            deployer = AutoTaggingDeployer(region=self.region)
            deployer.deploy()
            return True
        except Exception as e:
            logger.error(f"❌ Deployer failed: {e}")
            return False
        finally:
            os.chdir(original_cwd)
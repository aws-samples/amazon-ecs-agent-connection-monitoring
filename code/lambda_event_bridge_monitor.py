#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
"""This lambda function receives and processes ECS Events from event bridge.

Raises:
    ValueError: Missing values or mandatory properties.
"""
import json
import logging
import os
import sys
from commonlib.commonlib import ECSNode, SNSTopic

sys.tracebacklimit = 0

# create logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def custom_actions(expired_instance):
    """Allow the end user to implement any custom/personalized action and/or
    operation on the affected EC2 Instances.

    Args:
        expired_instance (list): A list of the affected EC2 Instances (ECS Agent disconnected)
    """
    pass


def handler(event, context):
    """Main function handler. The AWS Lambda function will receive ECS Events from
    AWS EventBridge, via an SQS Queue.

    This current function will:
     - Be invoked every time that there is Container Instance State Change with 'agentConnected' as false.
     - Evaluate if the Cluster where the Container Instance belongs is enabled for monitoring.
     - Check if the Instance is in ACTIVE status and is effectively running.
     - Send a notification via SNS.

    Args:
        event JSON: JSON structure containing the details of the ECS Agent disconnection.
        context JSON: JSON structure containing the execution context details.

    Raises:
       ValueError: Missing values or mandatory properties.
    """
    try:
        tag_key = os.environ.get("monitoringTagKeyName", None)
        tag_value = os.environ.get("monitoringTagKeyValue", None)
        check_all_clusters = os.environ.get("checkAllClusters", 'false')
        email_notification = os.environ.get("sendEmailNotification", False)

        logger.info("Start processing the event.")

        # Iterate over all the possible received events (SQS Messages)
        already_processed_nodes = []
        for record in event["Records"]:
            logger.info("Processing new record.")

            # Obtain payload
            payload = json.loads(record.get("body", None))

            # Sanity checks
            if payload is None:
                raise ValueError("Message empty! No details to process.")

            if (
                payload["source"] != "aws.ecs"
                or payload["detail-type"] != "ECS Container Instance State Change"
            ):
                raise ValueError(
                    "Only 'aws.ecs' events and Instance state changes are supported."
                )

            if (
                payload["detail"]["agentConnected"] is not False
                or payload["detail"]["status"] != "ACTIVE"
            ):
                logger.info(
                    "Instance does not need to be processed (reconnected or not ACTIVE)."
                )
                return

            # Create EC2 Instance object
            ecs_instance = ECSNode(
                payload.get("detail", None).get("containerInstanceArn", None),
                payload.get("detail", None).get("clusterArn", None),
            )

            # For the instance to be checked, it needs to meet 3 conditions:
            # 1. Is the Instance enabled for monitoring (cluster tags)?
            # 2. Is the ECS Container Instance running?
            # 3. Is the ECS Agent still disconnected?
            if (
                (
                    check_all_clusters.lower() in ['true', 'yes']
                    or ecs_instance.does_cluster_have_tags(
                        tag_key=tag_key, tag_value=tag_value
                    )
                )
                and ecs_instance.is_ec2_running()
                and not ecs_instance.is_agent_connected()
            ):
                # Store the node ID, as we will re-using it many times
                node_id = ecs_instance.get_ec2_item()['ec2InstanceId']

                if node_id in already_processed_nodes:
                    logger.info("Avoiding duplicated alerts, [%s] has already been processed.", node_id)

                else:
                    # Record the node ID, for avoiding future duplicates
                    already_processed_nodes.append(node_id)

                    # Sending the e-mail notification
                    notification_text = (
                        f"[ISSUE] ECS Container Instance {node_id}"
                        f" from Cluster {ecs_instance.get_ec2_item()['clusterArn']}"
                        f" has the ECS Agent disconnected."
                    )

                    logger.info("Sending email notification: %s", notification_text)
                    email_handler = SNSTopic()
                    email_handler.send_email(
                        topic_arn=email_notification,
                        email_subject=f"[ISSUE] ECS Instance - {node_id}",
                        email_body=notification_text,
                    )
                    # This log entry generates the CloudWatch metric
                    logger.warning(
                        "%s %s",
                        str(ecs_instance.get_ec2_item()["clusterArn"]).rsplit("/", 1)[1],
                        ecs_instance.get_ec2_item()["ec2InstanceId"],
                    )

                    # Execute custom actions
                    custom_actions(ecs_instance.get_ec2_item())
            else:
                logger.info(
                    "Container Instance %s does not need to be checked. Execution successful!",
                    ecs_instance.get_ec2_item()["ec2InstanceId"],
                )
        return

    except Exception as error:
        raise Exception(f"Error: execution error - {str(error)}") from error

#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
"""Defines common classes and modules."""
import logging
import re
import sys
import boto3
from botocore.exceptions import ClientError


class ECSNode:
    """Represents an ECS Container Instance, with all the inherited properties.
    It contains the metadata and methods for handling EC2 Container Instances
    or the ECS Anywhere node (whatever is applicable).

    Raises:
        ValueError: Missing values or mandatory properties.

    Returns:
        ContainerInstance: An instance of an ECS Container Instance object.
    """

    def __init__(self, node_ecs_arn: str, ecs_cluster: str):
        """Fetch and populate all the properties.

        Args:
            ec2_ecs_arn (String): ECS Container Instance ARN. Defaults to None.
            ecs_cluster (String): ECS Cluster. Defaults to None.
        """
        # Logging
        sys.tracebacklimit = 0
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)

        # Boto3 clients
        self.ecs_client = boto3.client("ecs")
        self.ec2_client = boto3.client("ec2")
        self.ssm_client = boto3.client("ssm")

        # Set Node properties (can be an EC2 Instance or an ECS Anywhere node)
        (
            self.ec2_id,
            self.instance_status,
            self.agent_connected,
        ) = self.fetch_ec2_details(node_ecs_arn, ecs_cluster)
        self.cluster_arn = ecs_cluster

    def fetch_ec2_details(self, node_ecs_arn: str, ecs_cluster: str):
        """Fetches and populates EC2 properties that must be retrieved from AWS API.

        Args:
            node_ecs_arn (String): ECS Container Instance ARN.
            ecs_cluster (String): ECS Cluster.

        Returns:
            String : node_id, instance_status, agent_connected - Instance properties
        """
        # Retrieve the ECS Node
        try:
            container_instance = self.ecs_client.describe_container_instances(
                cluster=ecs_cluster,
                containerInstances=[
                    node_ecs_arn,
                ],
            )["containerInstances"][0]
            node_id = container_instance["ec2InstanceId"]
            agent_connected = container_instance["agentConnected"]
            self.logger.info("Checking node [%s].", node_id)

            if re.compile(r"^i-(?:[a-f\d]{8}|[a-f\d]{17})$").match(node_id):
                # This is an EC2 Instance, retrieve the state
                instance_status = self.ec2_client.describe_instances(
                    InstanceIds=[node_id]
                )["Reservations"][0]["Instances"][0]["State"]["Name"]

            else:
                # This is an ECS Anywhere node, retrieve the state
                ping_status = self.ssm_client.describe_instance_information(
                    Filters=[
                        {"Key": "InstanceIds", "Values": [node_id]},
                    ],
                )["InstanceInformationList"][0]["PingStatus"]

                if ping_status == "Online":
                    instance_status = "running"
                else:
                    instance_status = "not-running"
    
            return node_id, instance_status, agent_connected

        except ClientError as error:
            code = error.response.get("Error", None).get("Code", None)
            message = error.response.get("Error", None).get("Message", None)
            raise Exception(f"Error: An error occurred when fetching instance details - {code} : {message}") from error
        
        except Exception as error:
            raise Exception(f"Error: An error occurred when fetching instance details - {str(error)}") from error

    def get_ec2_item(self):
        """Return an EC2 JSON item suitable.

        Returns:
            Dictionary : A dictionary containing all the properties.
        """
        return {
            "ec2InstanceId": self.ec2_id,
            "clusterArn": self.cluster_arn,
            "agentConnected": self.agent_connected,
        }

    def is_ec2_running(self) -> bool:
        """Check if the EC2 Instance is running or not.

        Returns:
            Boolean : True/False depending on the EC2 Instance state.
        """
        if self.instance_status == "running":
            return True

        self.logger.info("ECS Container Instance [%s] is not running anymore.", self.ec2_id)
        return False

    def does_cluster_have_tags(self, tag_key=None, tag_value=None) -> bool:
        """Check if the Cluster has a specific Tag.

        Args:
            tag_key (String, optional): Tag key to check. Defaults to None.
            tag_value (String, optional): Tag value to check. Defaults to None.

        Returns:
            Boolean : True/False depending on if the tag was found or not.
        """
        if tag_key is None or tag_value is None:
            return False

        # Fetch cluster tags
        try:
            cluster_tags = self.ecs_client.describe_clusters(
                clusters=[
                    self.cluster_arn,
                ],
                include=["TAGS"],
            )["clusters"][0]["tags"]

            # Check if tags match
            self.logger.info("Checking ECS cluster: [%s]", self.cluster_arn)
            self.logger.info("Looking for -> Tag key: [%s] - Tag value: [%s]", tag_key, tag_value)
            self.logger.info("Cluster Tags: %s", cluster_tags)

            tag_result = [ element for element in cluster_tags if (element['key'] == tag_key and element['value'] == tag_value) ]

            if tag_result:
                self.logger.info("Found tags: %s", str(tag_result))
                return True

            self.logger.info("ECS Cluster tags do not match. 'MonitorByTag' was enabled but this Cluster is not enabled for monitoring.")
            return False

        except ClientError as error:
            code = error.response.get("Error", None).get("Code", None)
            message = error.response.get("Error", None).get("Message", None)
            raise Exception(f"Error: An error occurred when checking Cluster tags - {code} : {message}") from error

        except Exception as error:
            raise Exception("Error: An error occurred when checking Cluster tags.") from error

    def is_agent_connected(self) -> bool:
        """Check if the ECS Agent has reconnected or not.

        Returns:
            Boolean : True/False depending on the ECS Agent state.
        """
        if self.agent_connected:
            self.logger.info("ECS Agent for Container Instance [%s] is connected.", self.ec2_id)
            return True
        else:
            self.logger.info("ECS Agent for Container Instance [%s] remains with agentConnected status as false.", self.ec2_id)
            return False


class SNSTopic:
    """Represents an AWS SNS Topic.

    Raises:
        ValueError: Missing values or mandatory properties.

    Returns:
        N / A.
    """

    def __init__(self):

        # Boto3 client
        self.sns_client = boto3.client("sns")

    def send_email(self, topic_arn=None, email_subject=None, email_body=None):
        """Sends the notification email.

        Args:
            topic_arn (String, optional): Destination SNS topic.
            email_subject (String, optional): Title for the email.
            email_body (String, optional): Body for the email.

        Returns:
            Boolean : True/False depending on if the tag was found or not.
        """
        try:
            self.sns_client.publish(
                TopicArn=topic_arn, Message=email_body, Subject=email_subject
            )

        except ClientError as error:
            code = error.response.get("Error", None).get("Code", None)
            message = error.response.get("Error", None).get("Message", None)
            raise Exception(f"Error: An error occurred when sending the e-mail - {code} : {message}") from error

        except Exception as error:
            raise Exception(f"Error: An error occurred when sending the e-mail - {str(error)}") from error

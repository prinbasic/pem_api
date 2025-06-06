pipeline {
    agent any

    triggers {
        githubPush()  // Trigger build on GitHub push
    }

    environment {
        BRANCH_NAME = "${env.BRANCH_NAME}"  // Correctly quote the variable
        AWS_REGION = 'ap-south-1'  // Set your AWS region
        DOCKER_REGISTRY = '676206929524.dkr.ecr.ap-south-1.amazonaws.com'  // ECR registry URL
        DOCKER_IMAGE = 'orbit/pem'  // ECR repository and image name
        DOCKER_TAG = "${DOCKER_IMAGE}:${BUILD_NUMBER}"
    }

    stages {
        stage('Checkout') {
            when {
                branch 'main'  // Only trigger on main branch
            }
            steps {
                checkout scm
            }
        }

        stage('Inject .env from Jenkins Secret File') {
            when {
                branch 'main'
            }
            steps {
                withCredentials([file(credentialsId: 'prinenvpem', variable: 'ENV_FILE')]) {
                    sh 'cp $ENV_FILE .env'
                }
            }
        }

        stage('Setup Python Env & Install Dependencies') {
            when {
                branch 'main'
            }
            steps {
                sh '''
                python3 -m venv venv
                source venv/bin/activate
                pip install --upgrade pip
                pip install -r requirements.txt
                '''
            }
        }

        stage('Login to AWS ECR') {
            steps {
                script {
                    // Authenticate to AWS ECR using the AWS CLI and Jenkins credentials
                    withCredentials([usernamePassword(credentialsId: 'aws-credentials', usernameVariable: 'AWS_ACCESS_KEY_ID', passwordVariable: 'AWS_SECRET_ACCESS_KEY')]) {
                        sh """
                            aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${DOCKER_REGISTRY}
                        """
                    }
                }
            }
        }

        stage('Build Docker Image') {
            steps {
                script {
                    // Build Docker image
                    sh """
                        docker build -t ${DOCKER_TAG} .
                    """
                }
            }
        }

        stage('Tag Docker Image') {
            steps {
                script {
                    // Tag the Docker image with the correct repository path
                    sh """
                        docker tag ${DOCKER_TAG} ${DOCKER_REGISTRY}/${DOCKER_TAG}
                    """
                }
            }
        }

        stage('Push Docker Image to ECR') {
            steps {
                script {
                    // Push the Docker image to AWS ECR
                    sh """
                        docker push ${DOCKER_REGISTRY}/${DOCKER_TAG}
                    """
                }
            }
        }
        stage('Stop and Remove Old Docker Container Running on Port 8000') {
            steps {
                script {
                    // Stop and remove the container running on port 8000
                    sh """
                        container_id=\$(docker ps -q --filter "publish=8000")
                        if [ -n "\$container_id" ]; then
                            docker stop \$container_id
                            docker rm \$container_id
                            echo 'Old container stopped and removed'
                        else
                            echo 'No container running on port 8000'
                        fi
                    """
                }
            }
        }

        stage('Run New Docker Container on Port 8000') {
            steps {
                script {
                    // Run the new Docker image on port 8000 with necessary environment variables
                    withCredentials([aws(credentialsId: 'aws-credentials')])
                        sh """
                            docker run -d -p 8000:8000 -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID} -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY} ${DOCKER_TAG}
                        """
                }
            }
        }
    }

    post {
        success {
            slackSend (
                tokenCredentialId: 'slack_channel_secret',
                message: "✅ Build SUCCESSFUL: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
                channel: '#jenekin_update',
                color: 'good',
                iconEmoji: ':white_check_mark:',
                username: 'Jenkins'
            )
        }
        failure {
            slackSend (
                tokenCredentialId: 'slack_channel_secret',
                message: "❌ Build FAILED: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
                channel: '#jenekin_update',
                color: 'danger',
                iconEmoji: ':x:',
                username: 'Jenkins'
            )
        }
        always {
            cleanWs()
        }
    }
}

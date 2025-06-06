pipeline {
    agent any

    triggers {
        githubPush()  // Trigger build on GitHub push
    }

    environment {
        BRANCH_NAME = "${env.BRANCH_NAME}"  // Correctly quote the variable
        AWS_REGION = 'ap-south-1'  // Set your AWS region
        DOCKER_REGISTRY = '676206929524.dkr.ecr.ap-south-1.amazonaws.com'  // ECR registry URL
        DOCKER_IMAGE = 'my-repository/pem-api'  // ECR repository and image name
        DOCKER_TAG = "${DOCKER_IMAGE}:${BUILD_NUMBER}"
    }

    stages {
        stage('Checkout') {
            when {
                branch 'main'  // Only trigger on main
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
                    // Authenticate to AWS ECR using AWS CLI (no plugin needed)
                    sh """
                        aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${DOCKER_REGISTRY}
                    """
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
    }

    post {
        success {
            slackSend (
            tokenCredentialId: 'slack_channel_secret',  // The ID of the bot token credential in Jenkins
            message: "✅ Build SUCCESSFUL: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
            channel: '#jenekin_update',
            color: 'good',  // Optional: Green color for success
            iconEmoji: ':white_check_mark:',  // Optional: Emoji
            username: 'Jenkins'
        )
        }
        failure {
            slackSend (
            tokenCredentialId: 'slack_channel_secret',
            message: "❌ Build FAILED: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
            channel: '#jenekin_update',
            color: 'danger',  // Optional: Red color for failure
            iconEmoji: ':x:',  // Optional: Emoji
            username: 'Jenkins'
        )
        }
        always {
            cleanWs()
        }
    }
}

pipeline {
    agent {
        label "${env.BRANCH_NAME == 'dev_main' ? 'dev-agent' : 'main'}"
    }

    triggers {
        githubPush()  // Trigger build on GitHub push
    }

    environment {
        BRANCH_NAME = "${env.BRANCH_NAME}"  // Correctly quote the variable
        AWS_REGION = 'ap-south-1'  // Set your AWS region
        DOCKER_REGISTRY = '676206929524.dkr.ecr.ap-south-1.amazonaws.com'  // ECR registry URL
        DOCKER_IMAGE = 'dev-orbit-pem'  // ECR repository and image name
        DOCKER_TAG = "${DOCKER_IMAGE}:${BUILD_NUMBER}"
        
    }

    stages {
        // Debugging the branch outside any stage
        stage('Initial Debug') {
            steps {
                script {
                    echo "Global Branch Debug: Branch name is: ${env.BRANCH_NAME}"
                    sh 'git rev-parse --abbrev-ref HEAD' // This will print the current git branch name
                }
            }
        }

        stage('Checkout') {
            // when {
            //     expression { return  env.GIT_BRANCH == 'refs/heads/main' }
            // }
            steps {
                checkout scm
            }
        }

        stage('Inject .env') {
            steps {
                script {
                // Choose the secret .env strictly by branch
                def envCredId
                switch (env.BRANCH_NAME) {
                    case 'dev_main':
                    envCredId = 'pem-dev'   // Jenkins "Secret file" credential for DEV
                    break
                    case 'main':
                    envCredId = 'pem-main'  // Jenkins "Secret file" credential for MAIN/PROD
                    break
                    default:
                    error "Unsupported branch '${env.BRANCH_NAME}'. Only 'dev_main' and 'main' are allowed."
                }

                echo "Using .env from credentials: ${envCredId}"

                withCredentials([file(credentialsId: envCredId, variable: 'ENV_FILE')]) {
                    sh 'cp "$ENV_FILE" .env'
                    // Optional sanity check without leaking secrets:
                    // sh '[ -s .env ] || { echo ".env missing or empty"; exit 1; }'
                }
                }
            }
        }

        stage('Setup Python Env & Install Dependencies') {
            // when {
            //     branch 'main'
            // }
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
                    withCredentials([usernamePassword(credentialsId: 'aws-credentials', usernameVariable: 'AWS_ACCESS_KEY_ID', passwordVariable: 'AWS_SECRET_ACCESS_KEY')]) {
                        sh """
                            # Login to AWS ECR
                            aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${DOCKER_REGISTRY}

                            # Run Docker container on port 8000 and pass AWS credentials to the container
                            docker run -d -p 8000:8000 \
                                -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID} \
                                -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY} \
                                ${DOCKER_REGISTRY}/${DOCKER_TAG}
                        """
                    }
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

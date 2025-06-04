pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Inject .env from Jenkins Secret File') {
            steps {
                withCredentials([file(credentialsId: 'prinenvpem', variable: 'ENV_FILE')]) {
                    sh 'cp $ENV_FILE .env'
                }
            }pipeline {
    agent any

    triggers {
        githubPush()  // Trigger build on GitHub push
    }

    environment {
        BRANCH_NAME = env.BRANCH_NAME
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
    }

    post {
        success {
            slackSend (
                webhookUrl: credentials('slack-webhook-url'),
                message: "✅ Build SUCCESSFUL: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
                channel: '#your-channel-name'
            )
        }
        failure {
            slackSend (
                webhookUrl: credentials('slack-webhook-url'),
                message: "❌ Build FAILED: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
                channel: '#your-channel-name'
            )
        }
    }
        always {
            cleanWs()
        }
    }


        }

        stage('Setup Python Env & Install Dependencies') {
            steps {
                sh '''
                python3 -m venv venv
                source venv/bin/activate
                pip install --upgrade pip
                pip install -r requirements.txt
                '''
            }
        }
    }

    post {
        always {
            cleanWs()
        }
    }
}

pipeline {
    agent any

    triggers {
        githubPush()  // Trigger build on GitHub push
    }

    environment {
        BRANCH_NAME = "${env.BRANCH_NAME}"  // Correctly quote the variable
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
            channel: '#orbit',  // Slack channel where you want to send the message
            message: "✅ Build SUCCESSFUL: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
            tokenCredentialId: 'slack_webhook_url',  // Use the credential ID for Slack webhook
            username: 'Jenkins',  // Optional: Set the bot's name
            iconEmoji: ':white_check_mark:',  // Optional: Set the bot's emoji icon
            color: 'good'  // Optional: Color of the message (green for success)
        )
        }
        failure {
            slackSend (
            channel: '#orbit',  // Slack channel where you want to send the message
            message: "❌ Build FAILED: ${env.JOB_NAME} [${env.BUILD_NUMBER}]",
            tokenCredentialId: 'slack_webhook_url',  // Use the credential ID for Slack webhook
            username: 'Jenkins',  // Optional: Set the bot's name
            iconEmoji: ':x:',  // Optional: Set the bot's emoji icon
            color: 'danger'  // Optional: Color of the message (red for failure)
        )
        }
        always {
            cleanWs()
        }
    }
}

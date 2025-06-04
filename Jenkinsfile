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

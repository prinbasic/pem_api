
# PEM API Base

This is a FastAPI-based backend service for PEM (Project Execution Management), specifically designed for CIBIL score checking, credit bureau integration, and lender matching for home loan applications. The system integrates with multiple external services including Equifax, CIBIL, OnGrid, and TransBank APIs.

---

## 🚀 Features
- **CIBIL Score Integration**: Multiple credit bureau integrations (Equifax, CIBIL, OnGrid)
- **OTP Verification**: SMS-based OTP verification for consent
- **Lender Matching**: Intelligent lender matching based on credit scores and property approvals
- **EMI Calculation**: Automated EMI calculations for loan offers
- **Report Generation**: AI-powered credit report generation
- **Caching**: 30-day TTL caching for credit reports
- **Fallback Mechanisms**: Multiple fallback strategies for API failures
- **High-performance FastAPI backend**
- **Modular architecture with API, models, and routes**
- **Dockerized setup for local and production environments**
- **CI/CD-ready with Jenkins pipeline configuration**

---

## 📂 Project Structure
```
pem_api_base/
├── api/                    # Business logic services
│   ├── cibil_service.py    # CIBIL/Equifax integration
│   ├── trans_service.py    # TransBank integration
│   ├── log_utils.py        # Logging utilities
│   └── signature.py        # AWS Lambda signature generation
├── models/                 # Pydantic models
│   └── request_models.py   # Request/response schemas
├── routes/                 # API endpoints
│   ├── cibil_routes.py     # CIBIL-related endpoints
│   ├── lender_routes.py    # Lender matching endpoints
│   └── trans_routes.py     # TransBank endpoints
├── main.py                 # FastAPI application entry point
├── db_client.py           # Database connection
├── dockerfile             # Docker configuration
├── requirements.txt       # Python dependencies
├── API_DOCUMENTATION.md   # Comprehensive API documentation
├── README.md              # Project documentation
└── .cursor/               # Cursor IDE configuration
```

---

## 📖 Documentation
- **Comprehensive API Documentation**: See [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- **OpenAPI Spec**: Available at `/openapi/aggregate.json`
- **Swagger UI**: Available at `/docs/aggregate`
- **ReDoc**: Available at `/redoc/aggregate`

---

## 📦 Setup

### 🐍 Local Development
1. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Set up environment variables** (see Environment Variables section below)
4. **Run the application**:
   ```bash
   uvicorn main:app --reload
   ```
5. **Access the API** at [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🔧 Environment Variables
Create a `.env` file with the following variables:

```bash
# Database Configuration
SUPABASE_DB_HOST=your-host
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=your-db-name
SUPABASE_DB_USER=your-username
SUPABASE_DB_PASSWORD=your-password

# API Endpoints
API_1_URL=https://api.equifax.com/initiate
API_2_URL=https://api.equifax.com/verify
API_3_URL=https://api.equifax.com/poll
API_4_URL=https://api.equifax.com/fetch
GRIDLINES_PAN_URL=https://api.gridlines.io/pan-api/fetch-detailed
GRIDLINES_API_KEY=your-gridlines-key
OTP_BASE_URL=https://api.orbit.basichomeloan.com/api_v1
BUREAU_PROFILE_URL=https://api.gridlines.io/bureau-profile
MOBILE_TO_PAN_URL=https://api.transbank.com/mobile-to-pan
MOBILE_TO_PREFILL_URL=https://api.transbank.com/mobile-to-prefill
PAN_SUPREME_URL=https://api.transbank.com/pan-supreme
CIBIL_URL=https://api.transbank.com/cibil
API_KEY=your-api-key
```

---

## 🐳 Docker Setup
1. **Build the Docker image**:
   ```bash
   docker build -t pem_api_base .
   ```
2. **Run the container**:
   ```bash
   docker run -d -p 8000:8000 pem_api_base
   ```
3. **Access the API** at [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🔐 Authentication
- **API Key Authentication**: Required for all `/cibil/*` endpoints
- **Header**: `x-api-key`
- **Trusted Auth**: `x-trusted-auth: yes` header required for direct access

---

## 🏗️ Architecture

### Technology Stack
- **Framework**: FastAPI 0.115.12
- **Database**: PostgreSQL (Supabase)
- **Authentication**: API Key-based authentication
- **External APIs**: Equifax, CIBIL, OnGrid, TransBank
- **AI Integration**: Orbit AI for report generation
- **Cloud**: AWS Lambda for signature generation

### Key Features
- **Async Operations**: Non-blocking I/O for external API calls
- **Comprehensive Error Handling**: Stages, flags, and reason codes
- **Caching Strategy**: 30-day TTL caching for credit reports
- **Fallback Mechanisms**: Multiple fallback strategies for API failures
- **Database Logging**: All operations logged to database

---

## 📊 API Endpoints

### Core Endpoints
- `POST /cibil/initiate-cibil` - Initiate CIBIL score check
- `GET /cibil/verify-otp` - Verify OTP for consent
- `GET /cibil/poll-consent` - Poll for consent status
- `POST /cibil/fetch-cibil-score` - Fetch CIBIL score
- `POST /cibil/fetch-lenders` - Fetch matching lenders
- `POST /cibil/intell-report` - Generate AI-powered credit report

### OnGrid Integration
- `POST /cibil/consent/send-otp` - Send OTP
- `POST /cibil/consent/resend-otp` - Resend OTP
- `POST /cibil/consent/verify-pan` - Verify PAN with OTP

### TransBank Integration
- `POST /cibil/primePan` - TransBank OTP and PAN verification

---

## 🗄️ Database Schema

### Main Tables
- `user_cibil_logs` - Credit check logs and reports
- `lenders` - Lender information and criteria
- `approved_projects` - Approved property projects
- `approved_projects_lenders` - Project-lender relationships

---

## 🔄 External Integrations

### Credit Bureaus
- **Equifax**: Primary credit bureau integration
- **CIBIL**: Alternative credit bureau via TransBank
- **OnGrid**: PAN verification and bureau profile

### Other Services
- **TransBank**: Mobile to PAN lookup and verification
- **Orbit AI**: AI-powered credit report generation
- **AWS Lambda**: Signature generation for external APIs

---

## ☸️ Kubernetes Deployment (To Be Added)
Deployment YAMLs will be added in a future update.

---

## 🤖 CI/CD with Jenkins
A Jenkins pipeline will:
- Build and test the application
- Build and push Docker images to a registry
- Deploy to Kubernetes

---

## 🧪 Testing
- Comprehensive error handling with stages and flags
- Fallback mechanism testing
- External API integration testing
- Database operation testing

---

## 📈 Monitoring & Logging
- **Health Endpoint**: `/cibil/health`
- **Database Logging**: All operations logged to `user_cibil_logs`
- **Error Tracking**: Comprehensive error flags and reason codes
- **Debug Information**: Detailed debug context for troubleshooting

---

## 🔒 Security
- **Data Protection**: Secure handling of personal information
- **API Keys**: Environment variable-based configuration
- **Database Security**: Connection string protection
- **HTTPS**: All external API calls over HTTPS

---

## 🙌 Contributing
1. Fork the repository
2. Create a new branch
3. Make changes and test
4. Follow the established error handling patterns
5. Submit a pull request

---

## 📄 License
This project is licensed under the MIT License.

---

## 👋 Contact
For questions or contributions, contact [Your Name or Organization].

---

## 📚 Additional Resources
- [Comprehensive API Documentation](API_DOCUMENTATION.md)
- [Cursor IDE Configuration](.cursor/rules)
- [OpenAPI Specification](http://localhost:8000/openapi/aggregate.json)
- [Interactive API Documentation](http://localhost:8000/docs/aggregate)

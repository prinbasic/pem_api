
# PEM API Base

This is a FastAPI-based backend service for PEM (Project Execution Management). It includes APIs, models, and routes, and is containerized for Docker and Kubernetes deployment. The project is CI/CD-ready for Jenkins integration.

---

## 🚀 Features
- High-performance FastAPI backend.
- Modular architecture with API, models, and routes.
- Dockerized setup for local and production environments.
- CI/CD-ready with Jenkins pipeline configuration.
- Kubernetes-ready with deployment configurations (to be added).

---

## 📂 Project Structure
```
pem_api_base/
├── api/              # API logic
├── models/           # Pydantic models
├── routes/           # API routes
├── main.py           # FastAPI entry point
├── dockerfile        # Docker configuration
├── requirements.txt  # Python dependencies
├── README.md         # Project documentation
├── .gitignore        # Git ignore rules
```

---

## 📦 Setup

### 🐍 Local Development
1. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scriptsctivate
   ```
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Run the application**:
   ```bash
   uvicorn main:app --reload
   ```

4. **Access the API** at [http://localhost:8000/docs](http://localhost:8000/docs)

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

## ☸️ Kubernetes Deployment (To Be Added)
Deployment YAMLs will be added in a future update.

---

## 🤖 CI/CD with Jenkins
A Jenkins pipeline will:
- Build and test the application.
- Build and push Docker images to a registry.
- Deploy to Kubernetes.

---

## 🙌 Contributing
1. Fork the repository.
2. Create a new branch.
3. Make changes and test.
4. Submit a pull request.

---

## 📄 License
This project is licensed under the MIT License.

---

## 👋 Contact
For questions or contributions, contact [Your Name or Organization].

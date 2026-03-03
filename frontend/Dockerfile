FROM node:20

WORKDIR /app

# copy only the inner project package files first for better caching
COPY frontend/package*.json ./frontend/

# install deps inside inner project
RUN cd frontend && npm install --legacy-peer-deps

# copy the rest of the inner project
COPY frontend ./frontend

WORKDIR /app/frontend

EXPOSE 3000

CMD ["npm", "run", "dev"]
FROM node:20-alpine

WORKDIR /app

# Copy package files from my-app subdirectory
COPY my-app/package*.json ./

RUN npm install --legacy-peer-deps

# Copy the entire Next.js project
COPY my-app/ .

EXPOSE 3000

CMD ["npm", "run", "dev"]
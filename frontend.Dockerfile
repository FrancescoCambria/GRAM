# Stage 1: Build the React app
FROM node:18 AS build

# Set the working directory
WORKDIR /app

# Copy package.json and package-lock.json
COPY minegraph-frontend/package.json minegraph-frontend/package-lock.json ./

# Install dependencies
RUN npm install

# Copy the rest of the application's code
COPY minegraph-frontend/ ./

# Build the app
RUN npm run build

# Stage 2: Serve the app with Nginx
FROM nginx:alpine

# Install envsubst if not present (usually present in alpine-nginx)
RUN apk add --no-cache gettext

# Copy the nginx config file
COPY minegraph-frontend/nginx.conf /etc/nginx/conf.d/default.conf.template

# Copy the build output to replace the default Nginx public folder
COPY --from=build /app/build /usr/share/nginx/html

# Copy and prepare the entrypoint script
COPY minegraph-frontend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Define the default environment variable
ENV BASE_PATH=/

ENTRYPOINT ["/entrypoint.sh"]

# Expose port 80
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]

# Build stage
FROM node:18-alpine AS build
WORKDIR /app

ARG REACT_APP_BACKEND_URL
ARG PUBLIC_URL

# Make the ARGs available as ENV for the build process
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
ENV PUBLIC_URL=$PUBLIC_URL

# Copy package.json and package-lock.json (if available)
# These are inside the frontend subdirectory
COPY gram-frontend/package.json gram-frontend/package-lock.json ./

# Install dependencies
RUN npm install

# Copy the rest of the frontend source code
COPY gram-frontend/ ./

# Build the React app
RUN npm run build

# Production stage
FROM nginx:alpine

# Copy the build artifacts from the build stage
COPY --from=build /app/build /usr/share/nginx/html

# Copy custom Nginx configuration if needed
COPY gram-frontend/nginx.conf /etc/nginx/conf.d/default.conf.template

# entrypoint.sh to handle environment variables in frontend
COPY gram-frontend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

# =============================================================================
# prod-ready-backend - Production Nginx Dockerfile
# Based on Alpine for minimal size and security
# =============================================================================

FROM nginx:1.27-alpine

# Install security updates
RUN apk update && \
    apk upgrade && \
    apk add --no-cache \
    ca-certificates \
    tzdata && \
    rm -rf /var/cache/apk/*

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ /etc/nginx/conf.d/

# Create directories for static files (if needed)
RUN mkdir -p /app/static /app/media && \
    chown -R nginx:nginx /app

# Create log directory with proper permissions
RUN mkdir -p /var/log/nginx && \
    chown -R nginx:nginx /var/log/nginx && \
    chown -R nginx:nginx /var/cache/nginx

# Set proper permissions for nginx user
RUN touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid /etc/nginx

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost/api/v1/system/health || exit 1

# Switch to non-root user
USER nginx

EXPOSE 80 443

STOPSIGNAL SIGQUIT

CMD ["nginx", "-g", "daemon off;"]

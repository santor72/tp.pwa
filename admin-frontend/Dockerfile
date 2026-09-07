FROM node:22-alpine AS build
WORKDIR /app
COPY admin-frontend/package.json ./
RUN npm install
COPY admin-frontend ./
ARG ADMIN_BASE_PATH=/admin/
ENV ADMIN_BASE_PATH=$ADMIN_BASE_PATH
RUN node -e "const p=process.env.ADMIN_BASE_PATH; if (!/^\\/[A-Za-z0-9._~-]+(?:\\/[A-Za-z0-9._~-]+)*\\/$/.test(p) || p.includes('..')) throw new Error('ADMIN_BASE_PATH must be an absolute path ending with /')"
RUN npm run build
RUN sed -i "s|__ADMIN_BASE_PATH__|$ADMIN_BASE_PATH|g" /app/nginx.conf

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080

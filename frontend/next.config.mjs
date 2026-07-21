/** @type {import('next').NextConfig} */
const API_GATEWAY = process.env.API_GATEWAY_URL || 'http://localhost:8000';

const nextConfig = {
  reactStrictMode: true,
  // Lint is run separately (`npm run lint`); don't fail production builds on it.
  eslint: { ignoreDuringBuilds: true },
  modularizeImports: {
    '@mui/icons-material': {
      transform: '@mui/icons-material/{{member}}',
    },
  },
  // Proxy REST calls to the FastAPI api-gateway so the browser talks to a
  // same-origin `/api/gateway/*` path (avoids CORS, keeps the JWT on one host).
  async rewrites() {
    return [
      {
        source: '/api/gateway/:path*',
        destination: `${API_GATEWAY}/:path*`,
      },
    ];
  },
};

export default nextConfig;

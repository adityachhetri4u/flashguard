import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: `${API_URL}/api`,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const getProducts = () => api.get('/products');
export const getProductInventory = (id) => api.get(`/products/${id}/inventory`);
export const reserveOrder = (data) => api.post('/orders/reserve', data);
export const payOrder = (orderId, simulateStatus) => api.post(`/orders/${orderId}/pay`, { simulate_status: simulateStatus });
export const getOrder = (orderId) => api.get(`/orders/${orderId}`);
export const getAdminMetrics = () => api.get('/admin/metrics');
export const seedDatabase = () => api.post('/admin/seed');

export default api;

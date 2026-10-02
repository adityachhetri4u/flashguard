import { useState, useEffect } from 'react';
import { getAdminMetrics, seedDatabase } from '../services/api';
import { Activity, Database, Server, RefreshCw, Box, AlertTriangle, Users } from 'lucide-react';

export default function AdminDashboard() {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 3000);
    return () => clearInterval(interval);
  }, []);

  const fetchMetrics = async () => {
    try {
      const res = await getAdminMetrics();
      setMetrics(res.data);
      setLoading(false);
    } catch (err) {
      console.error(err);
    }
  };

  const handleReset = async () => {
    if (window.confirm("Are you sure you want to reset the database and seed initial data?")) {
      setLoading(true);
      try {
        await seedDatabase();
        await fetchMetrics();
      } catch (err) {
        alert("Failed to seed data");
      }
    }
  };

  if (loading && !metrics) return <div className="text-center p-10">Loading Dashboard...</div>;

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex justify-between items-center bg-white p-6 rounded-xl shadow-sm border border-slate-200">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">FlashGuard System Monitor</h1>
          <p className="text-slate-500 text-sm">Real-time inventory and order tracking</p>
        </div>
        <button 
          onClick={handleReset}
          className="flex items-center px-4 py-2 bg-red-100 text-red-700 hover:bg-red-200 rounded-lg font-medium transition-colors"
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Seed / Reset Data
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <MetricCard title="Initial Inventory" value={metrics?.initial_inventory || 0} icon={Box} color="bg-blue-100 text-blue-700" />
        <MetricCard title="Available Now" value={metrics?.available_inventory || 0} icon={Database} color="bg-green-100 text-green-700" />
        <MetricCard title="Reserved (Pending)" value={metrics?.active_reservations || 0} icon={Activity} color="bg-orange-100 text-orange-700" />
        <MetricCard title="Confirmed Orders" value={metrics?.confirmed_orders || 0} icon={Users} color="bg-indigo-100 text-indigo-700" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <h2 className="text-lg font-bold text-slate-800 mb-4 border-b pb-2">Failure & Recovery Metrics</h2>
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <span className="text-slate-600">Total Purchase Attempts</span>
              <span className="font-bold text-slate-900">{metrics?.total_purchase_attempts || 0}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-600">Failed Orders (General)</span>
              <span className="font-bold text-slate-900">{metrics?.failed_orders || 0}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-600">Expired Reservations</span>
              <span className="font-bold text-slate-900">{metrics?.expired_reservations || 0}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-600">Payment Failures</span>
              <span className="font-bold text-slate-900">{metrics?.payment_failures || 0}</span>
            </div>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <h2 className="text-lg font-bold text-slate-800 mb-4 border-b pb-2">System Health</h2>
          <div className="space-y-4">
            <HealthRow name="API Server" status={metrics?.system?.api || 'UNKNOWN'} />
            <HealthRow name="Redis Cluster" status={metrics?.system?.redis || 'UNKNOWN'} />
            <HealthRow name="PostgreSQL DB" status={metrics?.system?.postgresql || 'UNKNOWN'} />
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricCard({ title, value, icon: Icon, color }) {
  return (
    <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200 flex items-center space-x-4">
      <div className={`p-4 rounded-full ${color}`}>
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <p className="text-sm text-slate-500 font-medium">{title}</p>
        <p className="text-2xl font-bold text-slate-900">{value}</p>
      </div>
    </div>
  );
}

function HealthRow({ name, status }) {
  const isHealthy = status === 'HEALTHY';
  return (
    <div className="flex justify-between items-center p-3 bg-slate-50 rounded-lg">
      <div className="flex items-center space-x-3">
        <Server className="w-5 h-5 text-slate-500" />
        <span className="font-medium text-slate-700">{name}</span>
      </div>
      <div className={`flex items-center px-3 py-1 rounded-full text-sm font-bold ${isHealthy ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
        {isHealthy ? <CheckCircle className="w-4 h-4 mr-1" /> : <AlertTriangle className="w-4 h-4 mr-1" />}
        {status}
      </div>
    </div>
  );
}

import { CheckCircle } from 'lucide-react';

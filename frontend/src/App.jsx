import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import FlashSale from './pages/FlashSale';
import AdminDashboard from './pages/AdminDashboard';
import { ShoppingCart, ShieldAlert } from 'lucide-react';

function Navbar() {
  return (
    <nav className="bg-slate-900 text-white p-4 shadow-lg">
      <div className="container mx-auto flex justify-between items-center">
        <div className="flex items-center space-x-2">
          <ShieldAlert className="h-6 w-6 text-red-500" />
          <Link to="/" className="text-xl font-bold tracking-wider">FLASHGUARD</Link>
        </div>
        <div className="flex space-x-6">
          <Link to="/" className="hover:text-red-400 flex items-center space-x-1">
            <ShoppingCart className="h-4 w-4" />
            <span>Store</span>
          </Link>
          <Link to="/admin" className="hover:text-red-400">Admin</Link>
        </div>
      </div>
    </nav>
  );
}

function App() {
  return (
    <Router>
      <div className="min-h-screen bg-slate-50">
        <Navbar />
        <main className="container mx-auto px-4 py-8">
          <Routes>
            <Route path="/" element={<FlashSale />} />
            <Route path="/admin" element={<AdminDashboard />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;

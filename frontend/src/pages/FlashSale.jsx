import { useState, useEffect, useRef } from 'react';
import { getProducts, getProductInventory, reserveOrder, payOrder } from '../services/api';
import { Clock, AlertCircle, CheckCircle, CreditCard, Loader2 } from 'lucide-react';

export default function FlashSale() {
  const [product, setProduct] = useState(null);
  const [inventory, setInventory] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('AVAILABLE'); // AVAILABLE, RESERVING, PAYMENT_PENDING, PROCESSING_PAYMENT, CONFIRMED, FAILED
  const [reservation, setReservation] = useState(null);
  const [paymentResult, setPaymentResult] = useState('');
  
  const idempotencyKey = useRef(crypto.randomUUID());

  useEffect(() => {
    fetchProduct();
    const interval = setInterval(fetchInventory, 2000);
    return () => clearInterval(interval);
  }, []);

  const fetchProduct = async () => {
    try {
      const res = await getProducts();
      if (res.data.length > 0) {
        setProduct(res.data[0]);
        setInventory(res.data[0].available_inventory);
      }
      setLoading(false);
    } catch (err) {
      setError('Failed to load product');
      setLoading(false);
    }
  };

  const fetchInventory = async () => {
    if (!product) return;
    try {
      const res = await getProductInventory(product.id);
      setInventory(res.data.available_inventory);
    } catch (err) {
      console.error(err);
    }
  };

  const handleBuy = async () => {
    setStatus('RESERVING');
    setError('');
    try {
      const userId = `user_${Math.floor(Math.random() * 10000)}`;
      const res = await reserveOrder({
        user_id: userId,
        product_id: product.id,
        quantity: 1,
        idempotency_key: idempotencyKey.current
      });
      setReservation(res.data);
      setStatus('PAYMENT_PENDING');
    } catch (err) {
      const errorMsg = err.response?.data?.detail || 'Reservation failed';
      setError(errorMsg);
      setStatus('AVAILABLE');
      if (errorMsg !== 'DUPLICATE_REQUEST') {
        idempotencyKey.current = crypto.randomUUID(); // reset on actual failure
      }
    }
  };

  const handlePayment = async (simulateStatus) => {
    setStatus('PROCESSING_PAYMENT');
    try {
      if (!reservation || !reservation.order_id) {
        throw new Error("No order found");
      }
      await payOrder(reservation.order_id, simulateStatus);
      if (simulateStatus === 'SUCCESS') {
        setStatus('CONFIRMED');
      } else {
        setStatus('AVAILABLE');
        idempotencyKey.current = crypto.randomUUID(); // reset for retry
        setError(`Payment ${simulateStatus.toLowerCase()}. Reservation cancelled.`);
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Payment simulation failed');
      setStatus('AVAILABLE');
      idempotencyKey.current = crypto.randomUUID();
    }
  };

  window.completePayment = handlePayment; // Expose for the buttons below



  // Temporary: let's rewrite the handleBuy to expect order_id in the response or use a new endpoint.
  return (
    <div className="max-w-4xl mx-auto mt-10">
      <div className="bg-white rounded-xl shadow-xl overflow-hidden">
        <div className="md:flex">
          <div className="md:w-1/2 bg-slate-900 p-8 text-white flex flex-col justify-center items-center">
            <h2 className="text-4xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-red-400 to-orange-500 mb-4">
              FLASH SALE
            </h2>
            <div className="text-6xl font-mono mb-6">00:14:59</div>
            <p className="text-slate-400">Ends soon! Limited stock.</p>
          </div>
          
          <div className="md:w-1/2 p-8">
            {loading ? (
              <div className="flex justify-center items-center h-full"><Loader2 className="animate-spin h-8 w-8 text-slate-500" /></div>
            ) : !product ? (
              <div className="text-center text-red-500">No active flash sale.</div>
            ) : (
              <div className="flex flex-col h-full justify-between">
                <div>
                  <h3 className="text-2xl font-bold text-slate-800 mb-2">{product.name}</h3>
                  <p className="text-slate-500 mb-6">{product.description}</p>
                  
                  <div className="flex justify-between items-end mb-6">
                    <div className="text-4xl font-bold text-slate-900">₹{product.price.toLocaleString()}</div>
                    <div className={`px-4 py-2 rounded-full font-bold ${inventory > 10 ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700 animate-pulse'}`}>
                      {inventory} left in stock
                    </div>
                  </div>
                </div>

                <div className="bg-slate-50 p-4 rounded-lg border border-slate-100 mb-6">
                  <div className="flex items-center space-x-2 text-sm text-slate-600 mb-2">
                    <Clock className="w-4 h-4" /> <span>Real-time inventory sync</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-2.5">
                    <div className="bg-gradient-to-r from-green-400 to-red-500 h-2.5 rounded-full" style={{ width: `${(inventory/product.total_inventory)*100}%` }}></div>
                  </div>
                </div>

                {error && (
                  <div className="mb-4 p-3 bg-red-100 text-red-700 rounded-lg flex items-center space-x-2">
                    <AlertCircle className="w-5 h-5" />
                    <span>{error}</span>
                  </div>
                )}

                {status === 'AVAILABLE' && (
                  <button 
                    onClick={handleBuy}
                    disabled={inventory === 0}
                    className="w-full py-4 bg-slate-900 hover:bg-slate-800 text-white rounded-lg font-bold text-lg transition-colors disabled:opacity-50 flex justify-center items-center space-x-2"
                  >
                    <span>BUY NOW</span>
                  </button>
                )}
                
                {status === 'RESERVING' && (
                  <button disabled className="w-full py-4 bg-slate-700 text-white rounded-lg font-bold text-lg flex justify-center items-center space-x-2">
                    <Loader2 className="animate-spin w-5 h-5" />
                    <span>Reserving...</span>
                  </button>
                )}

                {status === 'PAYMENT_PENDING' && (
                  <div className="space-y-3">
                    <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg text-blue-800">
                      <p className="font-bold mb-1">Inventory Reserved!</p>
                      <p className="text-sm">Complete your payment within 60 seconds.</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <button onClick={() => window.completePayment('SUCCESS')} className="py-3 bg-green-600 hover:bg-green-700 text-white rounded-lg font-bold flex justify-center items-center">
                        <CreditCard className="w-4 h-4 mr-2" /> Pay (Success)
                      </button>
                      <button onClick={() => window.completePayment('FAILED')} className="py-3 bg-red-600 hover:bg-red-700 text-white rounded-lg font-bold">
                        Pay (Fail)
                      </button>
                    </div>
                  </div>
                )}

                {status === 'PROCESSING_PAYMENT' && (
                  <button disabled className="w-full py-4 bg-slate-700 text-white rounded-lg font-bold text-lg flex justify-center items-center space-x-2">
                    <Loader2 className="animate-spin w-5 h-5" />
                    <span>Processing Payment...</span>
                  </button>
                )}

                {status === 'CONFIRMED' && (
                  <div className="p-6 bg-green-50 border border-green-200 rounded-lg text-center">
                    <CheckCircle className="w-12 h-12 text-green-500 mx-auto mb-2" />
                    <h4 className="text-xl font-bold text-green-800">Order Confirmed!</h4>
                    <p className="text-green-600 mt-1">Thank you for your purchase.</p>
                    <button onClick={() => {setStatus('AVAILABLE'); idempotencyKey.current = crypto.randomUUID();}} className="mt-4 text-sm text-green-700 underline">Buy another</button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

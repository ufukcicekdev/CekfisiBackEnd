import { Route } from 'react-router-dom';
import VerifyEmail from './pages/VerifyEmail';

const App = () => {
  return (
    <Route path="/auth/verify-email/:key" element={<VerifyEmail />} />
  );
};

export default App; 
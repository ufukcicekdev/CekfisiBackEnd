import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';

const VerifyEmail = () => {
    const { key } = useParams();  // URL'den key'i al
    const [status, setStatus] = useState('verifying');
    const [message, setMessage] = useState('');

    useEffect(() => {
        const verifyEmail = async () => {
            try {
                const response = await axios.get(
                    `${process.env.REACT_APP_API_URL}/api/v1/auth/verify-email/${key}/`
                );
                setStatus('success');
                setMessage(response.data.detail);
            } catch (error) {
                if (error.response?.data?.code === 'token_expired') {
                    // Token expired - yeni mail gönderildi
                    setStatus('expired');
                    setMessage(error.response.data.detail);
                } else {
                    setStatus('error');
                    setMessage(error.response?.data?.detail || 'Doğrulama sırasında bir hata oluştu.');
                }
            }
        };

        if (key) {
            verifyEmail();
        }
    }, [key]);

    return (
        <div>
            {status === 'verifying' && <p>Email doğrulanıyor...</p>}
            {status === 'success' && (
                <div>
                    <p>{message}</p>
                    <p>Giriş sayfasına yönlendiriliyorsunuz...</p>
                </div>
            )}
            {status === 'expired' && (
                <div>
                    <p>{message}</p>
                    <p>Lütfen yeni doğrulama emailinizi kontrol edin.</p>
                </div>
            )}
            {status === 'error' && (
                <div>
                    <p>{message}</p>
                    <p>Lütfen tekrar kayıt olun veya destek ekibiyle iletişime geçin.</p>
                </div>
            )}
        </div>
    );
};

export default VerifyEmail; 
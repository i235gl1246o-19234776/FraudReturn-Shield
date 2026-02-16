const express = require('express');
const router = express.Router();
const axios = require('axios');

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8080';

router.get('/', (req, res) => {
  res.render('index', { 
    title: 'Проверка возврата',
    backendUrl: BACKEND_URL
  });
});

router.get('/history', (req, res) => {
  res.render('history', { title: 'История проверок' });
});

router.get('/admin', (req, res) => {
  res.render('admin', { title: 'Настройки системы' });
});

router.post('/api/predict', async (req, res) => {
  try {
    const response = await axios.post(`${BACKEND_URL}/api/predict`, req.body);
    res.json(response.data);
  } catch (error) {
    res.status(500).json({ 
      error: 'Ошибка подключения к бэкенду',
      details: error.message 
    });
  }
});

router.get('/api/metadata', async (req, res) => {
  try {
    const response = await axios.get(`${BACKEND_URL}/api/metadata`);
    res.json(response.data);
  } catch (error) {
    res.status(500).json({ 
      error: 'Ошибка получения метаданных',
      details: error.message 
    });
  }
});

module.exports = router;
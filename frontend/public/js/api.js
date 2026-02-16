const API_BASE = '/api';

class FraudAPI {
  async predict(data) {
    try {
      const response = await fetch(`${API_BASE}/predict`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('API Error:', error);
      throw error;
    }
  }

  async getMetadata() {
    try {
      const response = await fetch(`${API_BASE}/metadata`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error('Metadata Error:', error);
      throw error;
    }
  }

  async getHistory(limit = 50) {
    try {
      const response = await fetch(`${API_BASE}/history?limit=${limit}`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error('History Error:', error);
      throw error;
    }
  }
}

const api = new FraudAPI();
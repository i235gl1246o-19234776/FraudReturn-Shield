package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html/template"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	_ "github.com/lib/pq"
	"golang.org/x/crypto/bcrypt"
)

// ============================================
// 📊 СТРУКТУРЫ ДАННЫХ
// ============================================

// FormData — данные формы проверки
// FormData — данные формы проверки (ОБНОВЛЁННАЯ ВЕРСИЯ)
type FormData struct {
	// === Основные поля из формы ===
	OrderNumber       string  `json:"orderNumber"`
	OrderAmount       float64 `json:"orderAmount"`
	AccountAgeDays    int     `json:"accountAgeDays"`
	TotalOrders       int     `json:"totalOrders"`
	TotalReturns      int     `json:"totalReturns"` // 🔧 ДОБАВЛЕНО
	ReturnRate        float64 `json:"returnRate"`
	DaysToReturn      int     `json:"daysToReturn"`
	Category          string  `json:"category"`
	AddressMatch      bool    `json:"addressMatch"`
	DeviceNew         bool    `json:"deviceNew"`
	IsWeekend         bool    `json:"isWeekend"`
	ClientID          int     `json:"clientID"`
	OrderID           int     `json:"orderID"`
	HasTag            bool    `json:"hasTag"`
	HasReceipt        bool    `json:"hasReceipt"`
	HasDamage         bool    `json:"hasDamage"`
	IsUsed            bool    `json:"isUsed"`
	Reason            string  `json:"reason"`
	DaysSincePurchase int     `json:"daysSincePurchase"`
	ReturnChannel     string  `json:"returnChannel"`
	TagsRemoved       bool    `json:"tagsRemoved"`
	MissingComponents bool    `json:"missingComponents"`

	// === Скрытые поля (из формы или БД) ===
	DiscountPercent         float64 `json:"discountPercent"`
	PromoCodeUsed           bool    `json:"promoCodeUsed"`
	FirstOrderDiscountAbuse bool    `json:"firstOrderDiscountAbuse"` // 🔧 ДОБАВЛЕНО
	ItemsInOrder            int     `json:"itemsInOrder"`
	IsElectronics           bool    `json:"isElectronics"`

	// === Риски и геолокация ===
	PaymentMethodRisk        float64 `json:"paymentMethodRisk"`
	ChargebackHistory90d     bool    `json:"chargebackHistory90d"`   // 🔧 ДОБАВЛЕНО
	CardBinCountryMismatch   bool    `json:"cardBinCountryMismatch"` // 🔧 ДОБАВЛЕНО
	ShippingRegionRisk       float64 `json:"shippingRegionRisk"`
	DeliveryAddressType      int     `json:"deliveryAddressType"` // 🔧 ДОБАВЛЕНО
	DistanceFromRegistration float64 `json:"distanceFromRegistration"`

	// === Время заказа ===
	OrderHour      int  `json:"orderHour"`      // 🔧 ДОБАВЛЕНО
	OrderTimeNight bool `json:"orderTimeNight"` // 🔧 ДОБАВЛЕНО

	// === IP/Device velocity ===
	IPVelocity24h     int  `json:"ipVelocity24h"`    // 🔧 ДОБАВЛЕНО
	IPVelocity7d      int  `json:"ipVelocity7d"`     // 🔧 ДОБАВЛЕНО
	AccountsPerIP     int  `json:"accountsPerIP"`    // 🔧 ДОБАВЛЕНО
	AccountsPerPhone  int  `json:"accountsPerPhone"` // 🔧 ДОБАВЛЕНО
	AccountsPerDevice int  `json:"accountsPerDevice"`
	DeviceIsEmulator  bool `json:"deviceIsEmulator"` // 🔧 ДОБАВЛЕНО

	// === Trust scores ===
	DeviceTrustScore float64 `json:"deviceTrustScore"`
	IPTrustScore     float64 `json:"ipTrustScore"`

	// === История и активность ===
	AvgOrderAmount    float64 `json:"avgOrderAmount"`    // 🔧 ДОБАВЛЕНО
	ReturnRate30d     float64 `json:"returnRate30d"`     // 🔧 ДОБАВЛЕНО
	RefundVelocity7d  int     `json:"refundVelocity7d"`  // 🔧 ДОБАВЛЕНО
	RefundVelocity30d int     `json:"refundVelocity30d"` // 🔧 ДОБАВЛЕНО
	SupportTickets30d int     `json:"supportTickets30d"`
	ReviewCount30d    int     `json:"reviewCount30d"`

	// === Контент и угрозы ===
	NegativeReviewCluster  bool `json:"negativeReviewCluster"`
	ThreatLanguageDetected bool `json:"threatLanguageDetected"`
	LegalClaimThreat       bool `json:"legalClaimThreat"`
}

// ResultData — результат расчёта риска
type ResultData struct {
	FormData
	RiskScore        float64  `json:"riskScore"`
	RiskLevel        string   `json:"riskLevel"`
	RiskClass        string   `json:"riskClass"`
	Recommendation   string   `json:"recommendation"`
	TopFactors       []string `json:"topFactors"`
	StrokeDashOffset float64  `json:"strokeDashOffset"`
	RiskPercent      int      `json:"riskPercent"`
	OrderID          int      `json:"orderID"`
	ClientID         int      `json:"clientID"`
}

// DBRecord — универсальная запись из БД
type DBRecord map[string]interface{}

// UserCard — данные для карточки пользователя
type UserCard struct {
	ClientID         int     `json:"client_id"`
	AccountAgeDays   int     `json:"account_age_days"`
	TotalOrders      int     `json:"total_orders"`
	TotalReturns     int     `json:"total_returns"`
	GlobalReturnRate float64 `json:"global_return_rate"`
	AvgOrderAmount   float64 `json:"avg_order_amount"`
	RiskLevel        string  `json:"risk_level"`
	LastActivity     string  `json:"last_activity"`
	Status           string  `json:"status"`
}

type User struct {
	ID       int    `json:"id"`
	Login    string `json:"login"`
	Password string `json:"-"`    // Не передаётся в JSON
	Role     string `json:"role"` // "admin" или "client"
}

// Глобальные переменные
var (
	pythonModelLoaded bool = false
	db                *sql.DB
)

func initDatabase() error {
	dbHost := getEnv("DB_HOST", "localhost")
	dbPort := getEnv("DB_PORT", "5432")
	dbUser := getEnv("DB_USER", "postgres")
	dbPassword := getEnv("DB_PASSWORD", "OmegaBloody13")
	dbName := getEnv("DB_NAME", "fraud_return_db")

	connStr := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable",
		dbHost, dbPort, dbUser, dbPassword, dbName)

	var err error
	db, err = sql.Open("postgres", connStr)
	if err != nil {
		return fmt.Errorf("ошибка открытия соединения: %w", err)
	}

	if err = db.Ping(); err != nil {
		return fmt.Errorf("ошибка подключения к БД: %w", err)
	}

	log.Println("✅ Подключение к PostgreSQL установлено")
	return nil
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func GetClientByID(clientID int) (*DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `SELECT client_id, account_age_days, total_orders, total_returns,
		global_return_rate, avg_order_amount, address_change_frequency,
		category_returns_count, created_at
		FROM clients WHERE client_id = $1`

	row := db.QueryRow(query, clientID)

	var record DBRecord = make(DBRecord)
	var createdAt time.Time
	var cid, accountAge, totalOrd, totalRet, catRetCount int
	var globalRate, avgAmt, addrFreq sql.NullFloat64

	err := row.Scan(
		&cid, &accountAge, &totalOrd, &totalRet,
		&globalRate, &avgAmt, &addrFreq, &catRetCount, &createdAt,
	)
	if err != nil {
		return nil, err
	}

	record["client_id"] = cid
	record["account_age_days"] = accountAge
	record["total_orders"] = totalOrd
	record["total_returns"] = totalRet
	if globalRate.Valid {
		record["global_return_rate"] = globalRate.Float64
	}
	if avgAmt.Valid {
		record["avg_order_amount"] = avgAmt.Float64
	}
	if addrFreq.Valid {
		record["address_change_frequency"] = addrFreq.Float64
	}
	record["category_returns_count"] = catRetCount
	record["created_at"] = createdAt.Format("2006-01-02 15:04:05")

	return &record, nil
}

func GetOrderByID(orderID int) (*DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `SELECT order_id, client_id, order_amount, items_count, discount_amount,
		payment_method, order_timestamp, amount_deviation, orders_last_30d
		FROM orders WHERE order_id = $1`

	row := db.QueryRow(query, orderID)

	var record DBRecord = make(DBRecord)
	var timestamp time.Time
	var oid, cid, itemsCnt, orders30d int
	var ordAmt, discAmt, amtDev sql.NullFloat64
	var payMethod sql.NullString

	err := row.Scan(
		&oid, &cid, &ordAmt, &itemsCnt, &discAmt,
		&payMethod, &timestamp, &amtDev, &orders30d,
	)
	if err != nil {
		return nil, err
	}

	record["order_id"] = oid
	record["client_id"] = cid
	if ordAmt.Valid {
		record["order_amount"] = ordAmt.Float64
	}
	record["items_count"] = itemsCnt
	if discAmt.Valid {
		record["discount_amount"] = discAmt.Float64
	}
	if payMethod.Valid {
		record["payment_method"] = payMethod.String
	}
	record["order_timestamp"] = timestamp.Format("2006-01-02 15:04:05")
	if amtDev.Valid {
		record["amount_deviation"] = amtDev.Float64
	}
	record["orders_last_30d"] = orders30d

	return &record, nil
}

func GetAllReturns(limit int) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `SELECT r.return_id, r.order_id, r.client_id, r.returns_last_30d,
		r.return_rate_last_30d, r.days_since_purchase, r.return_channel,
		r.has_receipt, r.tags_removed, r.missing_components,
		r.created_at, c.global_return_rate, o.order_amount
		FROM returns r
		LEFT JOIN clients c ON r.client_id = c.client_id
		LEFT JOIN orders o ON r.order_id = o.order_id
		ORDER BY r.created_at DESC
		LIMIT $1`

	rows, err := db.Query(query, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []DBRecord
	for rows.Next() {
		var record DBRecord = make(DBRecord)
		var createdAt time.Time
		var rid, oid, cid, ret30d, daysPurch int
		var retRate, globRate, ordAmt sql.NullFloat64
		var channel sql.NullString
		var hasRec, tagsRem, missComp bool

		err := rows.Scan(
			&rid, &oid, &cid, &ret30d, &retRate,
			&daysPurch, &channel, &hasRec, &tagsRem, &missComp,
			&createdAt, &globRate, &ordAmt,
		)
		if err != nil {
			return nil, err
		}

		record["return_id"] = rid
		record["order_id"] = oid
		record["client_id"] = cid
		record["returns_last_30d"] = ret30d
		if retRate.Valid {
			record["return_rate_last_30d"] = retRate.Float64
		}
		record["days_since_purchase"] = daysPurch
		if channel.Valid {
			record["return_channel"] = channel.String
		}
		record["has_receipt"] = hasRec
		record["tags_removed"] = tagsRem
		record["missing_components"] = missComp
		record["created_at"] = createdAt.Format("2006-01-02 15:04:05")
		if globRate.Valid {
			record["client_return_rate"] = globRate.Float64
		}
		if ordAmt.Valid {
			record["order_amount"] = ordAmt.Float64
		}

		results = append(results, record)
	}

	return results, nil
}

func GetStats() (map[string]interface{}, error) {
	if db == nil {
		return map[string]interface{}{
			"total_clients": 0,
			"total_orders":  0,
			"total_returns": 0,
			"high_risk":     0,
		}, nil
	}

	stats := make(map[string]interface{})

	var totalClients int
	db.QueryRow("SELECT COUNT(*) FROM clients").Scan(&totalClients)
	stats["total_clients"] = totalClients

	var totalOrders int
	db.QueryRow("SELECT COUNT(*) FROM orders").Scan(&totalOrders)
	stats["total_orders"] = totalOrders

	var totalReturns int
	db.QueryRow("SELECT COUNT(*) FROM returns").Scan(&totalReturns)
	stats["total_returns"] = totalReturns

	var highRisk int
	db.QueryRow("SELECT COUNT(*) FROM returns WHERE tags_removed = true OR has_receipt = false").Scan(&highRisk)
	stats["high_risk"] = highRisk

	return stats, nil
}

func SaveReturnToDB(form FormData) error {
	if db == nil {
		return fmt.Errorf("база данных не подключена")
	}

	if form.OrderID <= 0 || form.ClientID <= 0 {
		return nil
	}

	query := `INSERT INTO returns (order_id, client_id, days_since_purchase,
		has_receipt, tags_removed, missing_components,
		return_channel, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())`

	_, err := db.Exec(query,
		form.OrderID,
		form.ClientID,
		form.DaysSincePurchase,
		form.HasReceipt,
		form.TagsRemoved,
		form.MissingComponents,
		form.ReturnChannel,
	)

	return err
}

func CloseDB() {
	if db != nil {
		db.Close()
		log.Println("🔌 Соединение с БД закрыто")
	}
}

// apiGetOrders — GET /api/orders?client_id=1&q=...
func apiGetOrders(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	clientID := r.URL.Query().Get("client_id")
	query := r.URL.Query().Get("q")

	var orders []DBRecord
	var err error

	if clientID != "" {
		// 🔥 Поиск заказов конкретного клиента
		orders, err = GetOrdersByClientID(clientID, query)
	} else if query != "" {
		// Поиск по ID заказа
		order, err := GetOrderByID(parseInt(query))
		if err != nil {
			http.Error(w, `{"error":"Order not found"}`, http.StatusNotFound)
			return
		}
		orders = []DBRecord{*order}
	} else {
		// Последние 20 заказов
		orders, err = GetRecentOrders(20)
	}

	if err != nil {
		http.Error(w, `{"error":"Failed to fetch orders"}`, http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"orders": orders,
	})
}

// GetOrdersByClientID — получает заказы клиента с опциональным поиском
func GetOrdersByClientID(clientID, query string) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	var querySQL string
	var args []interface{}

	if query != "" {
		querySQL = `
            SELECT order_id, client_id, order_amount, items_count, 
                   payment_method, order_timestamp, amount_deviation
            FROM orders 
            WHERE client_id = $1 AND order_id::TEXT LIKE $2
            ORDER BY order_timestamp DESC
            LIMIT 20
        `
		args = []interface{}{clientID, "%" + query + "%"}
	} else {
		querySQL = `
            SELECT order_id, client_id, order_amount, items_count, 
                   payment_method, order_timestamp, amount_deviation
            FROM orders 
            WHERE client_id = $1
            ORDER BY order_timestamp DESC
            LIMIT 20
        `
		args = []interface{}{clientID}
	}

	rows, err := db.Query(querySQL, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []DBRecord
	for rows.Next() {
		var record DBRecord = make(DBRecord)
		var ts time.Time
		var orderID, clientID, itemsCount int
		var orderAmount, amountDev sql.NullFloat64
		var paymentMethod sql.NullString

		err := rows.Scan(
			&orderID, &clientID, &orderAmount, &itemsCount,
			&paymentMethod, &ts, &amountDev,
		)
		if err != nil {
			return nil, err
		}

		record["order_id"] = orderID
		record["client_id"] = clientID
		if orderAmount.Valid {
			record["order_amount"] = orderAmount.Float64
		}
		record["items_count"] = itemsCount
		if paymentMethod.Valid {
			record["payment_method"] = paymentMethod.String
		}
		record["order_timestamp"] = ts.Format("02.01.2006 15:04")
		if amountDev.Valid {
			record["amount_deviation"] = amountDev.Float64
		}

		results = append(results, record)
	}

	return results, nil
}

// GetRecentOrders — последние заказы
func GetRecentOrders(limit int) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `
        SELECT order_id, client_id, order_amount, items_count, 
               payment_method, order_timestamp, amount_deviation
        FROM orders 
        ORDER BY order_timestamp DESC
        LIMIT $1
    `

	rows, err := db.Query(query, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []DBRecord
	for rows.Next() {
		var record DBRecord = make(DBRecord)
		var ts time.Time
		var orderID, clientID, itemsCount int
		var orderAmount, amountDev sql.NullFloat64
		var paymentMethod sql.NullString

		err := rows.Scan(
			&orderID, &clientID, &orderAmount, &itemsCount,
			&paymentMethod, &ts, &amountDev,
		)
		if err != nil {
			return nil, err
		}

		record["order_id"] = orderID
		record["client_id"] = clientID
		if orderAmount.Valid {
			record["order_amount"] = orderAmount.Float64
		}
		record["items_count"] = itemsCount
		if paymentMethod.Valid {
			record["payment_method"] = paymentMethod.String
		}
		record["order_timestamp"] = ts.Format("02.01.2006 15:04")
		if amountDev.Valid {
			record["amount_deviation"] = amountDev.Float64
		}

		results = append(results, record)
	}

	return results, nil
}

// ============================================
// 👥 API: ПОЛЬЗОВАТЕЛИ (АДМИН-ПАНЕЛЬ)
// ============================================

// GetUsersList — список всех пользователей с пагинацией
func GetUsersList(page, limit int) ([]UserCard, int, error) {
	if db == nil {
		return nil, 0, fmt.Errorf("база данных не подключена")
	}

	// Считаем общее количество
	var total int
	db.QueryRow("SELECT COUNT(*) FROM clients").Scan(&total)

	offset := (page - 1) * limit

	query := `
		SELECT
			c.client_id,
			c.account_age_days,
			c.total_orders,
			c.total_returns,
			c.global_return_rate,
			c.avg_order_amount,
			CASE
				WHEN c.global_return_rate > 30 THEN 'high'
				WHEN c.global_return_rate > 15 THEN 'warning'
				ELSE 'active'
			END as risk_level,
			MAX(o.order_timestamp) as last_activity
		FROM clients c
		LEFT JOIN orders o ON c.client_id = o.client_id
		GROUP BY c.client_id
		ORDER BY last_activity DESC NULLS LAST
		LIMIT $1 OFFSET $2
	`

	rows, err := db.Query(query, limit, offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	var users []UserCard
	for rows.Next() {
		var u UserCard
		var lastActivity sql.NullTime
		err := rows.Scan(
			&u.ClientID, &u.AccountAgeDays, &u.TotalOrders,
			&u.TotalReturns, &u.GlobalReturnRate, &u.AvgOrderAmount,
			&u.RiskLevel, &lastActivity,
		)
		if err != nil {
			return nil, 0, err
		}
		if lastActivity.Valid {
			u.LastActivity = lastActivity.Time.Format("02.01.2006")
		} else {
			u.LastActivity = "—"
		}
		u.Status = u.RiskLevel
		users = append(users, u)
	}

	return users, total, nil
}

// GetUserOrders — заказы конкретного пользователя
func GetUserOrders(clientID, limit int) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `
		SELECT order_id, order_amount, items_count, payment_method,
		       order_timestamp, amount_deviation, order_status, product_category,
                       created_at
		FROM orders
		WHERE client_id = $1
		ORDER BY order_timestamp DESC
		LIMIT $2
	`

	rows, err := db.Query(query, clientID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var orders []DBRecord
	for rows.Next() {
		var record DBRecord = make(DBRecord)
		var ts time.Time
		var createdAt time.Time

		var orderID int
		var orderAmount sql.NullFloat64
		var itemsCount int
		var paymentMethod sql.NullString
		var amountDev sql.NullFloat64
		var orderStatus sql.NullString
		var productCategory sql.NullString

		err := rows.Scan(
			&orderID, &orderAmount, &itemsCount,
			&paymentMethod, &ts, &amountDev, &orderStatus, &productCategory, &createdAt,
		)
		if err != nil {
			return nil, err
		}

		record["order_id"] = orderID
		if orderAmount.Valid {
			record["order_amount"] = orderAmount.Float64
		}
		record["items_count"] = itemsCount
		if paymentMethod.Valid {
			record["payment_method"] = paymentMethod.String
		}
		record["order_timestamp"] = ts.Format("02.01.2006 15:04")
		if amountDev.Valid {
			record["amount_deviation"] = amountDev.Float64
		}
		if orderStatus.Valid {
			record["order_status"] = orderStatus.String
		} else {
			record["order_status"] = "completed"
		}
		if productCategory.Valid {
			record["product_category"] = productCategory.String
		}

		deliveryDate := ts.AddDate(0, 0, 7)
		record["delivery_date"] = deliveryDate.Format("02.01.2006")

		orders = append(orders, record)
	}
	return orders, nil
}

// GetUserReturns — возвраты конкретного пользователя
func GetUserReturns(clientID, limit int) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `
		SELECT return_id, order_id, days_since_purchase, return_channel,
		       has_receipt, tags_removed, missing_components, created_at
		FROM returns
		WHERE client_id = $1
		ORDER BY created_at DESC
		LIMIT $2
	`

	rows, err := db.Query(query, clientID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var returns []DBRecord
	for rows.Next() {
		var record DBRecord = make(DBRecord)
		var ts time.Time

		var returnID int
		var orderID int
		var daysSincePurchase int
		var returnChannel sql.NullString
		var hasReceipt bool
		var tagsRemoved bool
		var missingComponents bool

		err := rows.Scan(
			&returnID, &orderID, &daysSincePurchase,
			&returnChannel, &hasReceipt,
			&tagsRemoved, &missingComponents, &ts,
		)
		if err != nil {
			return nil, err
		}

		record["return_id"] = returnID
		record["order_id"] = orderID
		record["days_since_purchase"] = daysSincePurchase
		if returnChannel.Valid {
			record["return_channel"] = returnChannel.String
		}
		record["has_receipt"] = hasReceipt
		record["tags_removed"] = tagsRemoved
		record["missing_components"] = missingComponents
		record["created_at"] = ts.Format("02.01.2006 15:04")

		returns = append(returns, record)
	}
	return returns, nil
}

// ============================================
// 🔗 API HANDLERS
// ============================================

func apiGetClient(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error": "Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/client/")
	clientID, err := strconv.Atoi(path)
	if err != nil {
		http.Error(w, `{"error": "Invalid client ID"}`, http.StatusBadRequest)
		return
	}

	client, err := GetClientByID(clientID)
	if err != nil {
		http.Error(w, `{"error": "Client not found"}`, http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(client)
}

func apiGetOrder(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error": "Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/order/")
	orderID, err := strconv.Atoi(path)
	if err != nil {
		http.Error(w, `{"error": "Invalid order ID"}`, http.StatusBadRequest)
		return
	}

	order, err := GetOrderByID(orderID)
	if err != nil {
		http.Error(w, `{"error": "Order not found"}`, http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(order)
}

func apiGetStats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error": "Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	stats, err := GetStats()
	if err != nil {
		http.Error(w, `{"error": "Failed to get stats"}`, http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(stats)
}

// apiGetUsers — GET /api/users?page=1&limit=20
func apiGetUsers(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	page := 1
	limit := 20

	if p := r.URL.Query().Get("page"); p != "" {
		page, _ = strconv.Atoi(p)
		if page < 1 {
			page = 1
		}
	}
	if l := r.URL.Query().Get("limit"); l != "" {
		limit, _ = strconv.Atoi(l)
		if limit < 1 || limit > 100 {
			limit = 20
		}
	}

	users, total, err := GetUsersList(page, limit)
	if err != nil {
		http.Error(w, `{"error":"Failed to fetch users"}`, http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"users": users,
		"pagination": map[string]int{
			"page":  page,
			"limit": limit,
			"total": total,
			"pages": (total + limit - 1) / limit,
		},
	})
}

// apiGetUserDetail — GET /api/users/{id}
func apiGetUserDetail(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/users/")
	clientID, err := strconv.Atoi(path)
	if err != nil {
		http.Error(w, `{"error":"Invalid user ID"}`, http.StatusBadRequest)
		return
	}

	client, err := GetClientByID(clientID)
	if err != nil {
		http.Error(w, `{"error":"User not found"}`, http.StatusNotFound)
		return
	}

	orders, _ := GetUserOrders(clientID, 5)
	returns, _ := GetUserReturns(clientID, 5)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"client":         client,
		"recent_orders":  orders,
		"recent_returns": returns,
	})
}

// apiSearchUsers — GET /api/search/users?q=...
func apiSearchUsers(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	query := r.URL.Query().Get("q")
	if query == "" {
		http.Error(w, `{"error":"Search query is required"}`, http.StatusBadRequest)
		return
	}

	limit := 10
	if l := r.URL.Query().Get("limit"); l != "" {
		limit, _ = strconv.Atoi(l)
	}

	if id, err := strconv.Atoi(query); err == nil {
		user, err := GetClientByID(id)
		if err != nil {
			http.Error(w, `{"error":"User not found"}`, http.StatusNotFound)
			return
		}

		risk := "active"
		if rate, ok := (*user)["global_return_rate"].(float64); ok {
			if rate > 30 {
				risk = "high"
			} else if rate > 15 {
				risk = "warning"
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"results": []UserCard{{
				ClientID:         id,
				AccountAgeDays:   (*user)["account_age_days"].(int),
				TotalOrders:      (*user)["total_orders"].(int),
				TotalReturns:     (*user)["total_returns"].(int),
				GlobalReturnRate: (*user)["global_return_rate"].(float64),
				AvgOrderAmount:   (*user)["avg_order_amount"].(float64),
				RiskLevel:        risk,
				Status:           risk,
				LastActivity:     (*user)["created_at"].(string),
			}},
			"query": query,
			"limit": limit,
		})
		return
	}

	http.Error(w, `{"error":"User not found"}`, http.StatusNotFound)
}

// ============================================
// 📄 ОБРАБОТЧИКИ СТРАНИЦ
// ============================================

func loginPage(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("templates/login.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, nil); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

func clientProfileHandler(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("templates/client_profile.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, nil); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

func clientOrdersHandler(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("templates/client_orders.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, nil); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

func clientReturnsHandler(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("templates/client_returns.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, nil); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

func clientChatHandler(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("templates/client_chat.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, nil); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

func adminProfileHandler(w http.ResponseWriter, r *http.Request) {
	stats, err := GetStats()
	if err != nil {
		stats = map[string]interface{}{
			"total_clients": 0,
			"total_orders":  0,
			"total_returns": 0,
			"high_risk":     0,
		}
	}

	data := map[string]interface{}{
		"Stats":       stats,
		"DBConnected": db != nil,
	}

	tmpl, err := template.ParseFiles("templates/admin_profile.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, data); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

func authMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Проверяем наличие куки с пользователем
		cookie, err := r.Cookie("user")
		if err != nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		// Декодируем пользователя из куки
		userJSON, err := base64.StdEncoding.DecodeString(cookie.Value)
		if err != nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		var user map[string]interface{}
		if err := json.Unmarshal(userJSON, &user); err != nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		// Передаем информацию о пользователе в контекст запроса
		ctx := context.WithValue(r.Context(), "user", user)
		next(w, r.WithContext(ctx))
	}
}

// requireAdmin — middleware для проверки роли администратора
func requireAdmin(next http.HandlerFunc) http.HandlerFunc {
	return authMiddleware(func(w http.ResponseWriter, r *http.Request) {
		user, ok := r.Context().Value("user").(map[string]interface{})
		if !ok {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		role, ok := user["role"].(string)
		if !ok || role != "admin" {
			http.Error(w, "Доступ запрещён: требуется роль администратора", http.StatusForbidden)
			return
		}

		next(w, r)
	})
}

func homePage(w http.ResponseWriter, r *http.Request) {
	stats, err := GetStats()
	if err != nil {
		stats = map[string]interface{}{
			"total_clients": 0,
			"total_orders":  0,
			"total_returns": 0,
			"high_risk":     0,
		}
	}

	data := map[string]interface{}{
		"Stats":       stats,
		"DBConnected": db != nil,
	}

	tmpl, err := template.ParseFiles("templates/index.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, data); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

func checkHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		tmpl, err := template.ParseFiles("templates/check.html")
		if err != nil {
			http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
			return
		}
		tmpl.Execute(w, nil)
		return
	}

	if r.Method == http.MethodPost {
		r.ParseForm()

		// 1. Собираем базовые данные из формы
		form := FormData{
			ClientID:          parseInt(r.FormValue("clientID")),
			OrderID:           parseInt(r.FormValue("orderID")),
			Category:          r.FormValue("category"),
			Reason:            r.FormValue("reason"),
			AddressMatch:      r.FormValue("addressMatch") == "on",
			DeviceNew:         r.FormValue("deviceNew") == "on",
			IsWeekend:         r.FormValue("isWeekend") == "on",
			HasTag:            r.FormValue("hasTag") == "on",
			HasReceipt:        r.FormValue("hasReceipt") == "on",
			HasDamage:         r.FormValue("hasDamage") == "on",
			IsUsed:            r.FormValue("isUsed") == "on",
			TagsRemoved:       r.FormValue("tagsRemoved") == "on",
			MissingComponents: r.FormValue("missingComponents") == "on",
			DaysToReturn:      parseInt(r.FormValue("daysToReturn")),
		}

		// 2. 🔥 ЗАГРУЖАЕМ ВСЕ ОСТАЛЬНЫЕ ПРИЗНАКИ ИЗ БД
		if err := EnrichFromDB(&form); err != nil {
			log.Printf("[WARN] %v (используем дефолты)", err)
		}

		log.Printf("[DEBUG] 📝 Raw form: %+v", form)

		// 3. Лог для отладки
		log.Printf("[DEBUG] Final FormData: %+v", form)

		// 4. Сохраняем возврат
		if form.OrderID > 0 && form.ClientID > 0 {
			if err := SaveReturnToDB(form); err != nil {
				log.Printf("⚠️ DB Save error: %v", err)
			}
		}

		// 5. Считаем риск
		result := calculateRisk(form)

		// 6. Рендерим
		tmpl, err := template.ParseFiles("templates/result.html")
		if err != nil {
			http.Error(w, "Template error: "+err.Error(), 500)
			return
		}
		tmpl.Execute(w, result)
		return
	}

	http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
}

// EnrichFromDB загружает ВСЕ признаки из PostgreSQL
func EnrichFromDB(f *FormData) error {
	if db == nil || f.ClientID <= 0 || f.OrderID <= 0 {
		return fmt.Errorf("БД не подключена или не указаны ClientID/OrderID")
	}

	// ===== 1. Данные клиента =====
	clientQuery := `
		SELECT 
			account_age_days, 
			total_orders, 
			total_returns,
			global_return_rate,
			avg_order_amount,
			address_change_frequency
		FROM clients 
		WHERE client_id = $1
	`

	var accountAge, totalOrders, totalReturns int
	var globalRate, avgOrderAmt, addrFreq sql.NullFloat64

	err := db.QueryRow(clientQuery, f.ClientID).Scan(
		&accountAge, &totalOrders, &totalReturns,
		&globalRate, &avgOrderAmt, &addrFreq,
	)
	if err == nil {
		f.AccountAgeDays = accountAge
		f.TotalOrders = totalOrders
		if totalOrders > 0 {
			f.ReturnRate = float64(totalReturns) / float64(totalOrders) * 100
		} else {
			f.ReturnRate = 0
		}
		if avgOrderAmt.Valid {
			f.AvgOrderAmount = avgOrderAmt.Float64
		}
	}

	// ===== 2. Данные заказа =====
	orderQuery := `
		SELECT 
			order_amount,
			items_count,
			discount_amount,
			payment_method,
			order_timestamp
		FROM orders 
		WHERE order_id = $1 AND client_id = $2
	`

	var orderAmt, discAmt sql.NullFloat64
	var itemsCount int
	var payMethod sql.NullString
	var orderTS time.Time

	err = db.QueryRow(orderQuery, f.OrderID, f.ClientID).Scan(
		&orderAmt, &itemsCount, &discAmt, &payMethod, &orderTS,
	)
	if err == nil {
		if orderAmt.Valid {
			f.OrderAmount = orderAmt.Float64
		}
		f.ItemsInOrder = itemsCount
		if discAmt.Valid && f.OrderAmount > 0 {
			f.DiscountPercent = (discAmt.Float64 / f.OrderAmount) * 100
		}

		// Время заказа
		f.OrderHour = orderTS.Hour()
		f.IsWeekend = orderTS.Weekday() == time.Saturday || orderTS.Weekday() == time.Sunday
		if f.OrderHour >= 0 && f.OrderHour <= 5 {
			f.OrderTimeNight = true
		}
	}

	// ===== 3. Velocity признаки (из returns) =====
	velocityQuery := `
		SELECT COUNT(*) 
		FROM returns 
		WHERE client_id = $1 
		AND created_at > NOW() - INTERVAL '30 days'
	`
	var refundVelocity30d int
	db.QueryRow(velocityQuery, f.ClientID).Scan(&refundVelocity30d)
	f.RefundVelocity30d = refundVelocity30d

	// ===== 4. IP/Device velocity (из orders) =====
	ipVelocityQuery := `
		SELECT COUNT(DISTINCT o2.order_id)
		FROM orders o2
		WHERE o2.client_id = $1 
		AND o2.order_timestamp > NOW() - INTERVAL '24 hours'
	`
	var ipVel24h int
	db.QueryRow(ipVelocityQuery, f.ClientID).Scan(&ipVel24h)
	f.IPVelocity24h = ipVel24h

	ipVelocity7dQuery := `
		SELECT COUNT(DISTINCT o2.order_id)
		FROM orders o2
		WHERE o2.client_id = $1 
		AND o2.order_timestamp > NOW() - INTERVAL '7 days'
	`
	var ipVel7d int
	db.QueryRow(ipVelocity7dQuery, f.ClientID).Scan(&ipVel7d)
	f.IPVelocity7d = ipVel7d

	// ===== 5. Support tickets (из returns с проблемами) =====
	ticketsQuery := `
		SELECT COUNT(*) 
		FROM returns 
		WHERE client_id = $1 
		AND created_at > NOW() - INTERVAL '30 days'
		AND (tags_removed = true OR missing_components = true)
	`
	var supportTickets int
	db.QueryRow(ticketsQuery, f.ClientID).Scan(&supportTickets)
	f.SupportTickets30d = supportTickets

	// ===== 6. Accounts per IP/Device/Phone (агрегация) =====
	accountsPerIPQuery := `
		SELECT COUNT(DISTINCT o3.client_id)
		FROM orders o3
		JOIN orders o4 ON o3.client_id != o4.client_id
		WHERE o4.client_id = $1
		AND o3.order_timestamp > NOW() - INTERVAL '30 days'
		GROUP BY o3.client_id
		LIMIT 1
	`

	var accountsPerIP int
	err = db.QueryRow(accountsPerIPQuery, f.ClientID).Scan(&accountsPerIP)
	if err == nil && accountsPerIP > 0 {
		f.AccountsPerIP = accountsPerIP
	} else {
		f.AccountsPerIP = 1
	}
	f.AccountsPerDevice = 1
	f.AccountsPerPhone = 1

	// Упрощённая версия — берём из clients если есть
	if addrFreq.Valid {
		f.AccountsPerIP = int(addrFreq.Float64) + 1
	} else {
		f.AccountsPerIP = 1
	}
	f.AccountsPerDevice = 1
	f.AccountsPerPhone = 1

	// ===== 7. Payment method risk =====
	if payMethod.Valid {
		switch strings.ToLower(payMethod.String) {
		case "card", "online", "elektronno":
			f.PaymentMethodRisk = 0.1
		case "cash", "nalichnie", "cod":
			f.PaymentMethodRisk = 0.3
		default:
			f.PaymentMethodRisk = 0.2
		}
	}

	// ===== 8. Shipping region risk =====
	if addrFreq.Valid && addrFreq.Float64 > 2.0 {
		f.ShippingRegionRisk = 0.4
	} else {
		f.ShippingRegionRisk = 0.2
	}

	// ===== 9. Distance =====
	if addrFreq.Valid {
		f.DistanceFromRegistration = addrFreq.Float64 * 100
	} else {
		f.DistanceFromRegistration = 50
	}

	// ===== 10. Trust scores (вычисляем из истории) =====
	if f.TotalOrders > 0 {
		returnRate := float64(f.TotalReturns) / float64(f.TotalOrders)
		f.DeviceTrustScore = 1.0 - (returnRate * 0.5)
		f.IPTrustScore = 1.0 - (returnRate * 0.5)
		if f.DeviceTrustScore < 0.3 {
			f.DeviceTrustScore = 0.3
		}
		if f.IPTrustScore < 0.3 {
			f.IPTrustScore = 0.3
		}
	} else {
		f.DeviceTrustScore = 0.75
		f.IPTrustScore = 0.75
	}

	// ===== 11. Promo code used =====
	f.PromoCodeUsed = (f.DiscountPercent > 10)

	// ===== 12. First order discount abuse =====
	if f.TotalOrders == 1 && f.DiscountPercent > 20 {
		f.FirstOrderDiscountAbuse = true
	}

	// ===== 13. Review count =====
	f.ReviewCount30d = f.TotalOrders / 3 // примерная оценка

	// ===== 14. Return rate 30d =====
	if f.TotalOrders > 0 {
		f.ReturnRate30d = float64(refundVelocity30d) / float64(f.TotalOrders)
	}

	log.Printf("[DEBUG] DB-Enriched: AccountAge=%d, Orders=%d, Returns=%d, Velocity30d=%d",
		f.AccountAgeDays, f.TotalOrders, f.TotalReturns, refundVelocity30d)

	return nil
}

func settingsPage(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("templates/settings.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func historyPage(w http.ResponseWriter, r *http.Request) {
	returns, err := GetAllReturns(50)
	if err != nil {
		returns = []DBRecord{}
		log.Printf("⚠️ Не удалось загрузить историю из БД: %v", err)
	}

	data := map[string]interface{}{
		"Returns":     returns,
		"UseDatabase": db != nil && len(returns) > 0,
		"DBConnected": db != nil,
	}

	tmpl, err := template.ParseFiles("templates/history.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, data); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

// usersPage — Страница админ-панели пользователей
func usersPage(w http.ResponseWriter, r *http.Request) {
	users, total, err := GetUsersList(1, 20)
	if err != nil {
		users = []UserCard{}
		log.Printf("⚠️ Не удалось загрузить пользователей: %v", err)
	}

	// Подсчитываем статистику по уровням риска
	activeCount := 0
	warningCount := 0
	highRiskCount := 0
	for _, u := range users {
		switch u.RiskLevel {
		case "active":
			activeCount++
		case "warning":
			warningCount++
		case "high":
			highRiskCount++
		}
	}

	data := map[string]interface{}{
		"Users":         users,
		"Total":         total,
		"Page":          1,
		"Limit":         20,
		"ActiveCount":   activeCount,
		"WarningCount":  warningCount,
		"HighRiskCount": highRiskCount,
	}

	funcMap := template.FuncMap{
		"sub": func(a, b int) int { return a - b },
		"add": func(a, b int) int { return a + b },
	}

	tmpl, err := template.New("users.html").Funcs(funcMap).ParseFiles("templates/users.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}

	if err := tmpl.Execute(w, data); err != nil {
		http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
	}
}

// ============================================
// 🧮 ЛОГИКА РАСЧЁТА РИСКА
// ============================================

func calculateRisk(f FormData) ResultData {

	log.Printf("[DEBUG] FormData: %+v", f)
	score, err := predictRisk(f)

	if err != nil {
		log.Printf("⚠️ Python ONNX ошибка: %v, используем заглушку", err)
		score = calculateRiskFallback(f)
		log.Printf("[DEBUG] Fallback score: %.4f", score)
	}

	// Нормализация
	if score < 0 {
		score = 0
	}
	if score > 1 {
		score = 1
	}

	// // Обогащение из БД
	if db != nil && f.ClientID > 0 {
		enrichedScore, _ := enrichRiskFromDB(f.ClientID, float64(score))
		score = float32(enrichedScore)
	}

	level, class, recommendation := getRiskLevel(score)
	factors := getRiskFactors(f)

	log.Printf("[INFO] Final score: %.4f, Level: %s", score, level)

	return ResultData{
		FormData:         f,
		RiskScore:        float64(score),
		RiskLevel:        level,
		RiskClass:        class,
		Recommendation:   recommendation,
		TopFactors:       factors,
		StrokeDashOffset: (1 - float64(score)) * 283,
		RiskPercent:      int(float64(score) * 100),
		OrderID:          f.OrderID,
		ClientID:         f.ClientID,
	}
}

func enrichRiskFromDB(clientID int, baseScore float64) (float64, []string) {
	extraFactors := []string{}
	score := baseScore

	client, err := GetClientByID(clientID)
	if err != nil {
		return score, extraFactors
	}

	if rate, ok := (*client)["global_return_rate"].(float64); ok {
		if rate > 30 {
			score += 0.15
			extraFactors = append(extraFactors, "Высокий % возвратов у клиента")
		}
	}

	if age, ok := (*client)["account_age_days"].(int); ok {
		if age < 30 {
			score += 0.10
			extraFactors = append(extraFactors, "Новый аккаунт")
		}
	}

	if total, ok := (*client)["total_returns"].(int); ok {
		if total > 10 {
			score += 0.08
			extraFactors = append(extraFactors, "Много возвратов в истории")
		}
	}

	if freq, ok := (*client)["address_change_frequency"].(float64); ok {
		if freq > 2.0 {
			score += 0.07
			extraFactors = append(extraFactors, "Частая смена адресов")
		}
	}

	if score > 1.0 {
		score = 1.0
	}

	return score, extraFactors
}

func getRiskLevel(score float32) (string, string, string) {
	if score <= 0.30 {
		return "Низкий", "low", "✅ Автоматическое одобрение возврата"
	} else if score <= 0.65 {
		return "Средний", "medium", "⚠️ Требуется проверка оператором"
	}
	return "Высокий", "high", "❌ Требуется ручная верификация"
}

func prepareFeatures(f FormData) []float32 {
	features := make([]float32, 48)

	// 0-4: Основные (НОРМАЛИЗОВАННЫЕ как в обучении!)
	features[0] = float32(f.AccountAgeDays) / 730.0
	features[1] = float32(f.TotalOrders) / 100.0
	features[2] = float32(f.TotalReturns) / 50.0
	features[3] = float32(f.ReturnRate) / 100.0
	features[4] = float32(f.OrderAmount) / 200000.0

	// 5: Category
	catMap := map[string]float32{
		"electronics": 0, "clothing": 1, "cosmetics": 2,
		"books": 3, "sports": 4, "home": 5,
	}
	features[5] = catMap[f.Category]

	// 6-11: Флаги
	features[6] = b2f(f.OrderAmount > 30000)
	features[7] = b2f(f.IsWeekend)
	features[8] = b2f(f.AddressMatch)
	features[9] = b2f(f.DeviceNew)
	features[10] = b2f(f.HasReceipt)

	reasonMap := map[string]float32{
		"defect": 2, "size": 0, "color": 1,
		"quality": 2, "changed_mind": 1, "other": 14,
	}
	features[11] = reasonMap[f.Reason]

	// 12-16: Заказ
	features[12] = float32(f.DiscountPercent) / 50.0
	features[13] = b2f(f.PromoCodeUsed)
	features[14] = b2f(f.FirstOrderDiscountAbuse)
	features[15] = b2f(f.Category == "electronics")
	features[16] = float32(f.ItemsInOrder) / 10.0

	// 17-22: Риски
	features[17] = float32(f.PaymentMethodRisk)
	features[18] = b2f(f.ChargebackHistory90d)
	features[19] = b2f(f.CardBinCountryMismatch)
	features[20] = float32(f.ShippingRegionRisk)
	features[21] = float32(f.DeliveryAddressType) / 2.0
	features[22] = float32(f.DistanceFromRegistration) / 2000.0

	// 23-24: Время
	features[23] = float32(f.OrderHour) / 23.0
	features[24] = b2f(f.OrderTimeNight)

	// 25-29: IP/Accounts
	features[25] = float32(f.IPVelocity24h) / 20.0
	features[26] = float32(f.IPVelocity7d) / 50.0
	features[27] = float32(f.AccountsPerIP) / 10.0
	features[28] = float32(f.AccountsPerPhone) / 10.0
	features[29] = float32(f.AccountsPerDevice) / 10.0

	// 30-32: Trust
	features[30] = b2f(f.DeviceIsEmulator)
	features[31] = float32(f.DeviceTrustScore)
	features[32] = float32(f.IPTrustScore)

	// 33-34: История
	features[33] = float32(f.AvgOrderAmount) / 200000.0
	features[34] = float32(f.ReturnRate30d)

	// 35-38: Активность
	features[35] = float32(f.RefundVelocity7d) / 10.0
	features[36] = float32(f.RefundVelocity30d) / 20.0
	features[37] = float32(f.SupportTickets30d) / 10.0
	features[38] = float32(f.ReviewCount30d) / 20.0

	// 39-41: Контент
	features[39] = b2f(f.NegativeReviewCluster)
	features[40] = b2f(f.ThreatLanguageDetected)
	features[41] = b2f(f.LegalClaimThreat)

	features[42] = float32(f.ShippingRegionRisk)
	features[43] = float32(f.DistanceFromRegistration) / 2000.0
	features[44] = b2f(f.CardBinCountryMismatch)
	features[45] = b2f(f.ChargebackHistory90d)
	features[46] = b2f(f.ThreatLanguageDetected)
	features[47] = b2f(f.LegalClaimThreat)

	return features
}

func b2f(b bool) float32 {
	if b {
		return 1.0
	}
	return 0.0
}

func calculateRiskFallback(f FormData) float32 {
	score := 0.0

	// Признаки из формы
	if !f.HasTag {
		score += 0.25
	}
	if !f.HasReceipt {
		score += 0.15
	}
	if f.HasDamage {
		score += 0.20
	}
	if f.IsUsed {
		score += 0.25
	}
	if f.TagsRemoved {
		score += 0.20
	}
	if f.MissingComponents {
		score += 0.18
	}

	// Поведенческие признаки
	if f.Reason == "changed_mind" {
		score += 0.15
	}
	if f.DaysToReturn <= 3 {
		score += 0.10
	}
	if f.AccountAgeDays < 30 {
		score += 0.12
	}
	if f.ReturnRate > 30 {
		score += 0.18
	}
	if f.OrderAmount > 30000 {
		score += 0.10
	}

	// Дополнительные риски из формы
	if f.PaymentMethodRisk > 0.5 {
		score += 0.10
	}
	if f.ShippingRegionRisk > 0.5 {
		score += 0.08
	}
	if f.DistanceFromRegistration > 500 {
		score += 0.07
	}
	if f.DeviceTrustScore < 0.5 {
		score += 0.10
	}
	if f.IPTrustScore < 0.5 {
		score += 0.10
	}

	// Ограничиваем 0-1
	if score > 1.0 {
		score = 1.0
	}
	if score < 0.0 {
		score = 0.0
	}

	return float32(score)
}

func getRiskFactors(f FormData) []string {
	factors := []string{}

	if !f.HasTag {
		factors = append(factors, "🔴 Бирка отсутствует")
	}
	if !f.HasReceipt {
		factors = append(factors, "🔴 Чек не предоставлен")
	}
	if f.HasDamage {
		factors = append(factors, "⚠️ Есть повреждения товара")
	}
	if f.IsUsed {
		factors = append(factors, "⚠️ Товар имеет следы использования")
	}
	if f.Reason == "changed_mind" {
		factors = append(factors, "⚠️ Возврат без объективной причины")
	}
	if f.DaysToReturn <= 3 {
		factors = append(factors, "⚡ Очень быстрый возврат")
	}
	if f.AccountAgeDays < 30 {
		factors = append(factors, "🆕 Новый аккаунт")
	}
	if f.ReturnRate > 30 {
		factors = append(factors, "📈 Высокий % возвратов у клиента")
	}

	if len(factors) == 0 {
		factors = append(factors, "✅ Все параметры в норме")
	}

	return factors
}

// ============================================
// 🐍 PYTHON ONNX ИНТЕГРАЦИЯ
// ============================================

var fastAPIURL = "http://localhost:8000"

func callFastAPI(endpoint string, payload interface{}, result interface{}) error {
	url := fastAPIURL + endpoint

	jsonData, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("ошибка маршалинга JSON: %v", err)
	}

	resp, err := http.Post(url, "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		return fmt.Errorf("ошибка запроса к FastAPI: %v", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("ошибка чтения ответа: %v", err)
	}

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("FastAPI вернул статус %d: %s", resp.StatusCode, string(body))
	}

	if err := json.Unmarshal(body, result); err != nil {
		return fmt.Errorf("ошибка парсинга JSON ответа: %v", err)
	}

	return nil
}

type FastAPILoadModelRequest struct {
	ModelPath string `json:"model_path"`
	ModelType string `json:"model_type"`
}

type FastAPILoadModelResponse struct {
	Success bool   `json:"success"`
	Error   string `json:"error,omitempty"`
}

type FastAPIFraudFeatures struct {
	AccountAgeDays          int     `json:"account_age_days"`
	TotalPurchases          int     `json:"total_purchases"`
	TotalReturns            int     `json:"total_returns"`
	CustomerReturnRate      float64 `json:"customer_return_rate"`
	AvgOrderAmount          float64 `json:"avg_order_amount"`
	OrderAmount             float64 `json:"order_amount"`
	ItemsInOrder            int     `json:"items_in_order"`
	DiscountPercent         float64 `json:"discount_percent"`
	PaymentMethodRisk       float64 `json:"payment_method_risk"`
	AmountDeviation         float64 `json:"amount_deviation"`
	OrdersLast30d           int     `json:"orders_last_30d"`
	ReturnRate30d           float64 `json:"return_rate_30d"`
	RefundVelocity30d       int     `json:"refund_velocity_30d"`
	DaysSinceLastReturn     int     `json:"days_since_last_return"`
	DaysSincePurchase       int     `json:"days_since_purchase"`
	HasReceipt              int     `json:"has_receipt"`
	ReceiptProvided         int     `json:"receipt_provided"`
	TagsRemoved             int     `json:"tags_removed"`
	MissingComponents       int     `json:"missing_components"`
	OrderHour               int     `json:"order_hour"`
	HighValueFlag           int     `json:"high_value_flag"`
	OrderTimeNight          int     `json:"order_time_night"`
	FastReturnFlag          int     `json:"fast_return_flag"`
	NewAccountFlag          int     `json:"new_account_flag"`
	FirstOrderDiscountAbuse int     `json:"first_order_discount_abuse"`
	IsElectronics           int     `json:"is_electronics"`
	IPVelocity24h           int     `json:"ip_velocity_24h"`
	IPVelocity7d            int     `json:"ip_velocity_7d"`
	AccountsPerIP           int     `json:"accounts_per_ip"`
	AccountsPerPhone        int     `json:"accounts_per_phone"`
	AccountsPerDevice       int     `json:"accounts_per_device"`
	DeviceIsEmulator        int     `json:"device_is_emulator"`
	DeviceTrustScore        float64 `json:"device_trust_score"`
	IPTrustScore            float64 `json:"ip_trust_score"`
	AddressMatch            int     `json:"address_match"`
	DeviceNew               int     `json:"device_new"`
	PromoCodeUsed           int     `json:"promo_code_used"`
	WeekendPurchase         int     `json:"weekend_purchase"`
	RefundVelocity7d        int     `json:"refund_velocity_7d"`
	SupportTicketCount30d   int     `json:"support_ticket_count_30d"`
	ReviewCount30d          int     `json:"review_count_30d"`
	NegativeReviewCluster   int     `json:"negative_review_cluster"`
	ShippingRegionRisk      float64 `json:"shipping_region_risk"`
	DistanceFromRegCity     float64 `json:"distance_from_registration_city"`
	CardBinCountryMismatch  int     `json:"card_bin_country_mismatch"`
	ChargebackHistory90d    int     `json:"chargeback_history_90d"`
	ThreatLanguageDetected  int     `json:"threat_language_detected"`
	LegalClaimThreat        int     `json:"legal_claim_threat"`
}

type FastAPIFraudPredictionResponse struct {
	Success        bool    `json:"success"`
	Score          float64 `json:"score,omitempty"`
	Error          string  `json:"error,omitempty"`
	RiskLevel      string  `json:"risk_level,omitempty"`
	Recommendation string  `json:"recommendation,omitempty"`

	ReturnID         int     `json:"return_id,omitempty"`
	ClientID         int     `json:"client_id,omitempty"`
	OrderID          int     `json:"order_id,omitempty"`
	ProbabilityFraud float64 `json:"probability_fraud,omitempty"`
	AnomalyScore     float64 `json:"anomaly_score,omitempty"`
	IsAnomaly        bool    `json:"is_anomaly,omitempty"`
	CombinedScore    float64 `json:"combined_score,omitempty"`
	Decision         string  `json:"decision,omitempty"`
}

type FastAPIFraudPayloadRequest struct {
	ClientID            int     `json:"client_id"`
	OrderID             int     `json:"order_id"`
	ReturnID            int     `json:"return_id,omitempty"`
	AccountAgeDays      int     `json:"account_age_days"`
	TotalOrders         int     `json:"total_orders"`
	TotalReturns        int     `json:"total_returns"`
	GlobalReturnRate    float64 `json:"global_return_rate"`
	AvgOrderAmount      float64 `json:"avg_order_amount"`
	OrderAmount         float64 `json:"order_amount"`
	ItemsCount          int     `json:"items_count"`
	DiscountAmount      float64 `json:"discount_amount"`
	PaymentMethod       string  `json:"payment_method"`
	OrderTimestamp      string  `json:"order_timestamp,omitempty"`
	AmountDeviation     float64 `json:"amount_deviation"`
	OrdersLast30d       int     `json:"orders_last_30d"`
	ProductCategory     string  `json:"product_category"`
	IsElectronics       bool    `json:"is_electronics"`
	ShippingRegion      string  `json:"shipping_region"`
	RegionRiskScore     float64 `json:"region_risk_score"`
	DeliveryCity        string  `json:"delivery_city"`
	DistanceFromRegKm   float64 `json:"distance_from_registration_km"`
	PaymentCardBin      string  `json:"payment_card_bin,omitempty"`
	CardIssuingCountry  string  `json:"card_issuing_country,omitempty"`
	CardCountryMismatch bool    `json:"card_country_mismatch"`
	DeliveryAddressType string  `json:"delivery_address_type"`
	AddressMatchScore   float64 `json:"address_match_score"`
	IsAddressMatch      bool    `json:"is_address_match"`
	ReturnsLast30d      int     `json:"returns_last_30d"`
	ReturnRateLast30d   float64 `json:"return_rate_last_30d"`
	DaysSinceLastReturn int     `json:"days_since_last_return"`
	DaysSincePurchase   int     `json:"days_since_purchase"`
	ReturnChannel       string  `json:"return_channel"`
	HasReceipt          bool    `json:"has_receipt"`
	TagsRemoved         bool    `json:"tags_removed"`
	MissingComponents   bool    `json:"missing_components"`
	ClaimedReason       string  `json:"claimed_reason"`
}

type FastAPIChatRequest struct {
	Message string `json:"message"`
}

type FastAPIChatResponse struct {
	Response string `json:"response"`
	Error    string `json:"error,omitempty"`
}

func loadModel(modelPath string) error {
	req := FastAPILoadModelRequest{
		ModelPath: modelPath,
		ModelType: "fraud",
	}
	var resp FastAPILoadModelResponse

	err := callFastAPI("/api/load-models", req, &resp)
	if err != nil {
		return fmt.Errorf("ошибка загрузки fraud модели: %v", err)
	}

	if !resp.Success {
		return fmt.Errorf("%s", resp.Error)
	}

	log.Println("[INFO] Fraud модель ONNX загружена через FastAPI")
	return nil
}

func predictRisk(f FormData) (float32, error) {
	payload := FastAPIFraudPayloadRequest{
		ClientID:            f.ClientID,
		OrderID:             f.OrderID,
		ReturnID:            0, // return_id ещё нет, так как возврат создаётся
		AccountAgeDays:      f.AccountAgeDays,
		TotalOrders:         f.TotalOrders,
		TotalReturns:        f.TotalReturns,
		GlobalReturnRate:    f.ReturnRate,
		AvgOrderAmount:      f.AvgOrderAmount,
		OrderAmount:         f.OrderAmount,
		ItemsCount:          f.ItemsInOrder,
		DiscountAmount:      f.OrderAmount * f.DiscountPercent / 100.0,
		PaymentMethod:       "card", // Можно добавить в форму
		AmountDeviation:     0,      // Расчитывается в БД
		OrdersLast30d:       int(f.RefundVelocity30d),
		ProductCategory:     f.Category,
		IsElectronics:       f.IsElectronics,
		ShippingRegion:      "Moscow", // Можно добавить в форму
		RegionRiskScore:     f.ShippingRegionRisk,
		DeliveryCity:        "Moscow", // Можно добавить в форму
		DistanceFromRegKm:   f.DistanceFromRegistration,
		CardCountryMismatch: f.CardBinCountryMismatch,
		DeliveryAddressType: "home", // f.DeliveryAddressType - int, нужен string
		AddressMatchScore:   1.0,
		IsAddressMatch:      f.AddressMatch,
		ReturnsLast30d:      int(f.RefundVelocity30d),
		ReturnRateLast30d:   f.ReturnRate30d,
		DaysSinceLastReturn: 999, // Можно рассчитать
		DaysSincePurchase:   f.DaysSincePurchase,
		ReturnChannel:       f.ReturnChannel,
		HasReceipt:          f.HasReceipt,
		TagsRemoved:         f.TagsRemoved,
		MissingComponents:   f.MissingComponents,
		ClaimedReason:       f.Reason,
	}

	var resp FastAPIFraudPredictionResponse
	err := callFastAPI("/api/predict-fraud-payload", payload, &resp)
	if err != nil {
		log.Printf("[ERROR] FastAPI predict error: %v", err)
		return 0, err
	}

	if !resp.Success {
		return 0, fmt.Errorf("%s", resp.Error)
	}

	score := resp.CombinedScore
	if score == 0 {
		score = resp.ProbabilityFraud
	}

	log.Printf("[INFO] v4 prediction: combined=%.4f, prob=%.4f, decision=%s",
		resp.CombinedScore, resp.ProbabilityFraud, resp.Decision)
	return float32(score), nil
}

func parseInt(value string) int {
	if value == "" {
		return 0
	}
	i, err := strconv.Atoi(value)
	if err != nil {
		return 0
	}
	return i
}

func parseFloat(value string) float64 {
	if value == "" {
		return 0
	}
	f, err := strconv.ParseFloat(value, 64)
	if err != nil {
		return 0
	}
	return f
}

// ============================================
// 💬 CHAT API
// ============================================

// ChatRequest — запрос к чату
type ChatRequest struct {
	Message string `json:"message"`
}

// ChatResponse — ответ от чата
type ChatResponse struct {
	Response string `json:"response,omitempty"`
	Error    string `json:"error,omitempty"`
}

// handleChat — обработчик запросов к AI-помощнику
func handleChat(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(ChatResponse{Error: "Method not allowed"})
		return
	}

	var req ChatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ChatResponse{Error: "Invalid request body"})
		return
	}

	if req.Message == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ChatResponse{Error: "Message is required"})
		return
	}

	// Вызов Python-скрипта с ONNX моделью для генерации ответа
	response, err := callPythonModelForChat(req.Message)
	if err != nil {
		log.Printf("[ERROR] Chat model error: %v", err)
		json.NewEncoder(w).Encode(ChatResponse{
			Response: "Извините, я пока учусь и не могу ответить на этот вопрос. Попробуйте спросить что-то другое!",
		})
		return
	}

	json.NewEncoder(w).Encode(ChatResponse{Response: response})
}

func callPythonModelForChat(message string) (string, error) {
	req := FastAPIChatRequest{
		Message: message,
	}
	var resp FastAPIChatResponse

	err := callFastAPI("/api/chat", req, &resp)
	if err != nil {
		log.Printf("[ERROR] FastAPI chat error: %v", err)
		return "Сервис чата временно недоступен. Попробуйте позже.", nil
	}

	if resp.Error != "" {
		log.Printf("[ERROR] Chat API error: %s", resp.Error)
	}

	return resp.Response, nil

}

func apiLogin(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	var req struct {
		Login    string `json:"login"`
		Password string `json:"password"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid request body"})
		return
	}

	if db == nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "База данных не подключена"})
		return
	}

	// Поиск пользователя в таблице user_accounts
	query := `SELECT client_id, login, password_hash, role FROM user_accounts WHERE login = $1`
	row := db.QueryRow(query, req.Login)

	var foundUser User
	var passwordHash string
	err := row.Scan(&foundUser.ID, &foundUser.Login, &passwordHash, &foundUser.Role)
	if err != nil {
		if err == sql.ErrNoRows {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Неверный логин или пароль"})
			return
		}
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Ошибка при поиске пользователя"})
		return
	}

	// Проверка пароля с использованием bcrypt
	err = bcrypt.CompareHashAndPassword([]byte(passwordHash), []byte(req.Password))
	if err != nil {
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Неверный логин или пароль"})
		return
	}

	userData := map[string]interface{}{
		"id":    foundUser.ID,
		"login": foundUser.Login,
		"role":  foundUser.Role,
	}
	userJSON, _ := json.Marshal(userData)

	// Кодируем в base64 для безопасной передачи в куке
	encodedUser := base64.StdEncoding.EncodeToString(userJSON)

	http.SetCookie(w, &http.Cookie{
		Name:     "user",
		Value:    encodedUser,
		Path:     "/",
		MaxAge:   86400, // 24 часа
		HttpOnly: false, // false чтобы JS мог читать (для sessionStorage)
		SameSite: http.SameSiteLaxMode,
	})

	// Успешный вход
	json.NewEncoder(w).Encode(map[string]interface{}{
		"user": map[string]interface{}{
			"id":    foundUser.ID,
			"login": foundUser.Login,
			"role":  foundUser.Role,
		},
	})
}

func apiLogout(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	// Очищаем куку пользователя
	http.SetCookie(w, &http.Cookie{
		Name:     "user",
		Value:    "",
		Path:     "/",
		MaxAge:   -1, // Удаляем куку
		HttpOnly: false,
		SameSite: http.SameSiteLaxMode,
	})

	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": true,
	})
}

// apiGetClientOrders — получение заказов клиента
func apiGetClientOrders(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	// Получаем ID клиента из query параметра или из сессии
	clientIDStr := r.URL.Query().Get("client_id")
	if clientIDStr == "" {
		cookie, err := r.Cookie("user")
		if err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Unauthorized"})
			return
		}
		userJSON, err := base64.StdEncoding.DecodeString(cookie.Value)
		if err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid session"})
			return
		}
		var user map[string]interface{}
		if err := json.Unmarshal(userJSON, &user); err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid session"})
			return
		}
		clientIDFloat, ok := user["id"].(float64)
		if !ok {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid user ID"})
			return
		}
		clientIDStr = fmt.Sprintf("%d", int(clientIDFloat))
	}

	clientID, err := strconv.Atoi(clientIDStr)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid client ID"})
		return
	}

	// Получаем заказы из БД
	orders, err := GetUserOrders(clientID, 100)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to fetch orders: " + err.Error()})
		return
	}

	if orders == nil {
		orders = []DBRecord{}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"orders": orders,
	})
}

// apiGetClientReturns — получение возвратов клиента
func apiGetClientReturns(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	clientIDStr := r.URL.Query().Get("client_id")
	if clientIDStr == "" {
		// Пытаемся получить из куки
		cookie, err := r.Cookie("user")
		if err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Unauthorized"})
			return
		}
		// Декодируем пользователя из куки
		userJSON, err := base64.StdEncoding.DecodeString(cookie.Value)
		if err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid session"})
			return
		}
		var user map[string]interface{}
		if err := json.Unmarshal(userJSON, &user); err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid session"})
			return
		}
		clientIDFloat, ok := user["id"].(float64)
		if !ok {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid user ID"})
			return
		}
		clientIDStr = fmt.Sprintf("%d", int(clientIDFloat))
	}

	clientID, err := strconv.Atoi(clientIDStr)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid client ID"})
		return
	}

	// Получаем возвраты из БД
	returns, err := GetUserReturns(clientID, 100)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to fetch returns: " + err.Error()})
		return
	}

	if returns == nil {
		returns = []DBRecord{}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"returns": returns,
	})
}

// ============================================
// 🚀 ОСНОВНОЙ СЕРВЕР
// ============================================

func main() {
	pythonModelLoaded = false

	go startFastAPIService()

	execDir, err := os.Getwd()

	log.Println("[INFO] Ожидание запуска FastAPI сервиса...")
	time.Sleep(3 * time.Second)

	if err != nil {
		log.Fatalf("Не удалось получить рабочую директорию: %v", err)
	}

	modelPath := filepath.Join(execDir, "models", "fraud_model_v4_27patterns.onnx")

	if err := loadModel(modelPath); err != nil {
		log.Printf("⚠️ Не удалось загрузить модель: %v (будет использована заглушка)", err)
		pythonModelLoaded = false
	} else {
		pythonModelLoaded = true
		log.Println("✅ Модель успешно загружена")
	}

	if err := initDatabase(); err != nil {
		log.Printf("⚠️ Не удалось подключиться к БД: %v (работа продолжится без БД)", err)
	}
	defer CloseDB()

	staticDir := filepath.Join(execDir, "static")

	if _, err := os.Stat(staticDir); os.IsNotExist(err) {
		log.Fatalf("Директория static не найдена по пути: %s", staticDir)
	}

	http.HandleFunc("/static/", func(w http.ResponseWriter, r *http.Request) {
		relativePath := strings.TrimPrefix(r.URL.Path, "/static/")
		path := filepath.Join(staticDir, relativePath)

		// Проверяем, что файл существует
		if _, err := os.Stat(path); os.IsNotExist(err) {
			http.NotFound(w, r)
			return
		}

		// Устанавливаем правильные MIME-типы
		if strings.HasSuffix(path, ".css") {
			w.Header().Set("Content-Type", "text/css; charset=utf-8")
		} else if strings.HasSuffix(path, ".js") {
			w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
		} else if strings.HasSuffix(path, ".png") {
			w.Header().Set("Content-Type", "image/png")
		} else if strings.HasSuffix(path, ".jpg") || strings.HasSuffix(path, ".jpeg") {
			w.Header().Set("Content-Type", "image/jpeg")
		} else if strings.HasSuffix(path, ".svg") {
			w.Header().Set("Content-Type", "image/svg+xml")
		} else if strings.HasSuffix(path, ".ico") {
			w.Header().Set("Content-Type", "image/x-icon")
		}

		http.ServeFile(w, r, path)
	})

	// Страницы
	http.HandleFunc("/login", loginPage)
	http.HandleFunc("/admin/profile", requireAdmin(adminProfileHandler))
	http.HandleFunc("/client/orders", authMiddleware(clientOrdersHandler))
	http.HandleFunc("/client/returns", authMiddleware(clientReturnsHandler))
	http.HandleFunc("/client/chat", authMiddleware(clientChatHandler))
	http.HandleFunc("/client/profile", authMiddleware(clientProfileHandler))
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		cookie, err := r.Cookie("user")
		if err != nil {
			// Не авторизован — перенаправляем на login
			log.Printf("[DEBUG] Нет куки user: %v", err)
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		log.Printf("[DEBUG] Кука user: %s", cookie.Value)

		// Декодируем из base64
		decodedUser, err := base64.StdEncoding.DecodeString(cookie.Value)
		if err != nil {
			log.Printf("[DEBUG] Ошибка декодирования base64 из куки: %v", err)
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		// Декодируем информацию о пользователе из куки
		var userData struct {
			Role string `json:"role"`
		}
		if err := json.Unmarshal(decodedUser, &userData); err != nil {
			log.Printf("[DEBUG] Ошибка парсинга JSON из куки: %v", err)
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

		log.Printf("[DEBUG] Роль пользователя: %s", userData.Role)

		// Авторизован — перенаправляем в зависимости от роли
		if userData.Role == "admin" {
			http.Redirect(w, r, "/admin/profile", http.StatusSeeOther)
		} else {
			http.Redirect(w, r, "/client/profile", http.StatusSeeOther)
		}
	})
	http.HandleFunc("/check", requireAdmin(checkHandler))
	http.HandleFunc("/settings", requireAdmin(settingsPage))
	http.HandleFunc("/history", requireAdmin(historyPage))
	http.HandleFunc("/users", requireAdmin(usersPage))

	// API endpoints
	http.HandleFunc("/api/login", apiLogin)
	http.HandleFunc("/api/logout", apiLogout)
	http.HandleFunc("/api/client/orders", apiGetClientOrders)
	http.HandleFunc("/api/client/returns", apiGetClientReturns)
	http.HandleFunc("/api/client/", apiGetClient)
	http.HandleFunc("/api/order/", apiGetOrder)
	http.HandleFunc("/api/orders/", apiGetOrders)
	http.HandleFunc("/api/stats", apiGetStats)
	http.HandleFunc("/api/users", apiGetUsers)
	http.HandleFunc("/api/users/", apiGetUserDetail)
	http.HandleFunc("/api/search/users", apiSearchUsers)
	http.HandleFunc("/api/chat", handleChat)

	port := getEnv("PORT", ":8083")
	fmt.Printf("🛡️ FraudReturn Shield запущен на http://localhost%s\n", port)
	fmt.Printf("🐍 FastAPI сервис запущен на http://localhost:8000\n")

	if err := http.ListenAndServe(port, nil); err != nil {
		log.Fatal("❌ Ошибка запуска сервера:", err)
	}
}

func startFastAPIService() {
	wd, _ := os.Getwd()
	pythonPath := "python3"
	if _, err := exec.LookPath("python"); err == nil {
		pythonPath = "python"
	} else if _, err := exec.LookPath("py"); err == nil {
		pythonPath = "py"
	}
	scriptPath := filepath.Join(wd, "api.py")

	cmd := exec.Command(pythonPath, scriptPath)
	cmd.Dir = wd
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	log.Printf("[INFO] Запуск FastAPI сервиса: %s %s", pythonPath, scriptPath)

	if err := cmd.Run(); err != nil {
		log.Printf("[ERROR] FastAPI сервис остановился: %v", err)
	}
}

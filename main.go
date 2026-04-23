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
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime/debug"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "github.com/lib/pq"
	"golang.org/x/crypto/bcrypt"
)

type FormData struct {
	OrderNumber       string  `json:"orderNumber"`
	OrderAmount       float64 `json:"orderAmount"`
	AccountAgeDays    int     `json:"accountAgeDays"`
	TotalOrders       int     `json:"totalOrders"`
	TotalReturns      int     `json:"totalReturns"`
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
	ClaimedReason     string  `json:"claimed_reason"`
	DaysSincePurchase int     `json:"daysSincePurchase"`
	ReturnChannel     string  `json:"returnChannel"`
	TagsRemoved       bool    `json:"tagsRemoved"`
	MissingComponents bool    `json:"missingComponents"`

	DiscountPercent         float64 `json:"discountPercent"`
	PromoCodeUsed           bool    `json:"promoCodeUsed"`
	FirstOrderDiscountAbuse bool    `json:"firstOrderDiscountAbuse"`
	ItemsInOrder            int     `json:"itemsInOrder"`
	IsElectronics           bool    `json:"isElectronics"`

	PaymentMethodRisk        float64 `json:"paymentMethodRisk"`
	ChargebackHistory90d     bool    `json:"chargebackHistory90d"`
	CardBinCountryMismatch   bool    `json:"cardBinCountryMismatch"`
	ShippingRegionRisk       float64 `json:"shippingRegionRisk"`
	DeliveryAddressType      int     `json:"deliveryAddressType"`
	DistanceFromRegistration float64 `json:"distanceFromRegistration"`

	OrderHour      int  `json:"orderHour"`
	OrderTimeNight bool `json:"orderTimeNight"`

	IPVelocity24h     int  `json:"ipVelocity24h"`
	IPVelocity7d      int  `json:"ipVelocity7d"`
	AccountsPerIP     int  `json:"accountsPerIP"`
	AccountsPerPhone  int  `json:"accountsPerPhone"`
	AccountsPerDevice int  `json:"accountsPerDevice"`
	DeviceIsEmulator  bool `json:"deviceIsEmulator"`

	DeviceTrustScore float64 `json:"deviceTrustScore"`
	IPTrustScore     float64 `json:"ipTrustScore"`

	AvgOrderAmount    float64 `json:"avgOrderAmount"`
	ReturnRate30d     float64 `json:"returnRate30d"`
	RefundVelocity7d  int     `json:"refundVelocity7d"`
	RefundVelocity30d int     `json:"refundVelocity30d"`
	SupportTickets30d int     `json:"supportTickets30d"`
	ReviewCount30d    int     `json:"reviewCount30d"`

	NegativeReviewCluster  bool `json:"negativeReviewCluster"`
	ThreatLanguageDetected bool `json:"threatLanguageDetected"`
	LegalClaimThreat       bool `json:"legalClaimThreat"`
}

type FeatureExplanation struct {
	Feature      string  `json:"feature"`
	Contribution float64 `json:"contribution"`
	Effect       string  `json:"effect"`
	Label        string  `json:"label,omitempty"`
}

type ResultData struct {
	FormData
	RiskScore        float64              `json:"riskScore"`
	RiskLevel        string               `json:"riskLevel"`
	RiskClass        string               `json:"riskClass"`
	Recommendation   string               `json:"recommendation"`
	TopFactors       []string             `json:"topFactors"`
	TopFeatures      []FeatureExplanation `json:"topFeatures,omitempty"`
	StrokeDashOffset float64              `json:"strokeDashOffset"`
	RiskPercent      int                  `json:"riskPercent"`
	OrderID          int                  `json:"orderID"`
	ClientID         int                  `json:"clientID"`
}

type DBRecord map[string]interface{}

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
	Password string `json:"-"`
	Role     string `json:"role"`
}

type CreateReturnRequest struct {
	OrderID       int    `json:"order_id"`
	ClaimedReason string `json:"claimed_reason"`
	Comment       string `json:"comment"`
}

var (
	pythonModelLoaded bool = false
	db                *sql.DB
)

var featureLabelMap = map[string]string{
	"shipping_region_risk":            "Риск региона доставки",
	"payment_method_risk":             "Риск метода оплаты",
	"distance_from_registration_city": "Расстояние от города регистрации",
	"customer_return_rate":            "Процент возвратов клиента",
	"account_age_days":                "Возраст аккаунта",
	"order_amount":                    "Сумма заказа",
	"items_count":                     "Количество товаров в заказе",
	"discount_amount":                 "Размер скидки",
	"amount_deviation":                "Отклонение суммы от среднего",
	"orders_last_30d":                 "Заказы за 30 дней",
	"is_electronics":                  "Категория: электроника",
	"region_risk_score":               "Оценка риска региона",
	"card_country_mismatch":           "Несоответствие страны карты",
	"delivery_address_type":           "Тип адреса доставки",
	"address_match_score":             "Совпадение адресов",
	"is_address_match":                "Адрес совпадает с регистрацией",
	"returns_last_30d":                "Возвраты за 30 дней",
	"return_rate_last_30d":            "Процент возвратов за 30 дней",
	"days_since_last_return":          "Дней с последнего возврата",
	"days_since_purchase":             "Дней с момента покупки",
	"return_channel":                  "Канал возврата",
	"has_receipt":                     "Наличие чека",
	"tags_removed":                    "Бирки удалены",
	"missing_components":              "Отсутствуют компоненты",
	"claimed_reason":                  "Причина возврата",
	"device_trust_score":              "Доверие к устройству",
	"ip_trust_score":                  "Доверие к IP",
	"ip_velocity_24h":                 "Активность IP за 24ч",
	"accounts_per_device":             "Аккаунтов на устройство",
	"chargeback_history_90d":          "История чарджбэков",
	"threat_language_detected":        "Обнаружены угрозы в тексте",
	"legal_claim_threat":              "Угроза юридического иска",
	"global_return_rate":              "Глобальный % возвратов",
	"avg_order_amount":                "Средняя сумма заказа",
	"product_category":                "Категория товара",
	"payment_card_bin":                "BIN карты",
	"card_issuing_country":            "Страна эмитента карты",
}

func getFeatureLabel(featureName string) string {
	if label, ok := featureLabelMap[featureName]; ok {
		return label
	}
	words := strings.Split(featureName, "_")
	for i := range words {
		words[i] = strings.Title(words[i])
	}
	return strings.Join(words, " ")
}

func setCORSHeaders(w http.ResponseWriter, r *http.Request) {
	origin := r.Header.Get("Origin")
	allowedOrigins := []string{
		"http://localhost:3000",
		"http://127.0.0.1:3000",
		"http://localhost:8080",
		"http://localhost:8083",
		"http://127.0.0.1:8083",
	}

	for _, o := range allowedOrigins {
		if origin == o {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			break
		}
	}
	w.Header().Set("Access-Control-Allow-Credentials", "true")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
}

func applyJSONHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
}

func getClientIDFromRequest(r *http.Request) (int, error) {
	clientIDStr := r.URL.Query().Get("client_id")

	if clientIDStr == "" {
		cookie, err := r.Cookie("user")
		if err != nil {
			return 0, fmt.Errorf("no user cookie")
		}

		userJSON, err := base64.StdEncoding.DecodeString(cookie.Value)
		if err != nil {
			return 0, fmt.Errorf("invalid cookie encoding")
		}

		var user map[string]interface{}
		if err := json.Unmarshal(userJSON, &user); err != nil {
			return 0, fmt.Errorf("invalid user JSON")
		}

		clientIDFloat, ok := user["id"].(float64)
		if !ok {
			return 0, fmt.Errorf("invalid user ID type")
		}
		clientIDStr = fmt.Sprintf("%d", int(clientIDFloat))
	}

	clientID, err := strconv.Atoi(clientIDStr)
	if err != nil || clientID <= 0 {
		return 0, fmt.Errorf("invalid client ID: %s", clientIDStr)
	}

	return clientID, nil
}

func authMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		cookie, err := r.Cookie("user")
		if err != nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}

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

		ctx := context.WithValue(r.Context(), "user", user)
		next(w, r.WithContext(ctx))
	}
}

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

	query := `SELECT o.order_id, o.client_id, o.order_amount, o.items_count, o.discount_amount,
o.payment_method, o.order_timestamp, o.amount_deviation, o.orders_last_30d, o.product_category,
r.days_since_purchase, r.claimed_reason, r.return_channel
FROM orders o
LEFT JOIN returns r ON o.order_id = r.order_id
WHERE o.order_id = $1`

	row := db.QueryRow(query, orderID)

	var record DBRecord = make(DBRecord)
	var timestamp time.Time
	var oid, cid, itemsCnt, orders30d, daysSincePurchase sql.NullInt64
	var ordAmt, discAmt, amtDev sql.NullFloat64
	var payMethod, prodCategory, claimedReason, returnChannel sql.NullString

	err := row.Scan(
		&oid, &cid, &ordAmt, &itemsCnt, &discAmt,
		&payMethod, &timestamp, &amtDev, &orders30d, &prodCategory,
		&daysSincePurchase, &claimedReason, &returnChannel,
	)
	if err != nil {
		return nil, err
	}

	record["order_id"] = oid.Int64
	record["client_id"] = cid.Int64
	if ordAmt.Valid {
		record["order_amount"] = ordAmt.Float64
	}
	if itemsCnt.Valid {
		record["items_count"] = itemsCnt.Int64
	}
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
	if orders30d.Valid {
		record["orders_last_30d"] = orders30d.Int64
	}
	if prodCategory.Valid {
		record["product_category"] = prodCategory.String
	}
	if daysSincePurchase.Valid {
		record["days_to_return"] = daysSincePurchase.Int64
	}
	if claimedReason.Valid {
		record["reason"] = claimedReason.String
	}
	if returnChannel.Valid {
		record["return_channel"] = returnChannel.String
	}
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
                return_channel, claimed_reason, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())`

	_, err := db.Exec(query,
		form.OrderID,
		form.ClientID,
		form.DaysSincePurchase,
		form.HasReceipt,
		form.TagsRemoved,
		form.MissingComponents,
		form.ReturnChannel,
		form.ClaimedReason,
	)

	return err
}

func SaveCheckHistory(result ResultData) error {
	if db == nil {
		return fmt.Errorf("база данных не подключена")
	}

	if result.OrderID <= 0 || result.ClientID <= 0 {
		return nil
	}

	var factorsStr string
	if len(result.TopFactors) == 0 {
		factorsStr = "{}"
	} else {
		factorsStr = "{"
		for i, f := range result.TopFactors {
			escaped := strings.ReplaceAll(f, `"`, `\"`)
			escaped = strings.ReplaceAll(escaped, `\`, `\\`)
			if i > 0 {
				factorsStr += ","
			}
			factorsStr += `"` + escaped + `"`
		}
		factorsStr += "}"
	}

	query := `INSERT INTO check_history
                (order_id, client_id, order_amount, risk_score, risk_level, risk_class, recommendation, top_factors, checked_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())`

	_, err := db.Exec(query,
		result.OrderID,
		result.ClientID,
		result.FormData.OrderAmount,
		result.RiskScore,
		result.RiskLevel,
		result.RiskClass,
		result.Recommendation,
		factorsStr,
	)

	return err
}

func GetAllCheckHistory(limit int) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `SELECT check_id, order_id, client_id, order_amount, risk_score, risk_level, risk_class, recommendation, checked_at
                FROM check_history ORDER BY checked_at DESC LIMIT $1`

	rows, err := db.Query(query, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []DBRecord
	for rows.Next() {
		var record DBRecord = make(DBRecord)
		var checkedAt time.Time
		var checkID, orderID, clientID int
		var orderAmt, riskScore sql.NullFloat64
		var riskLevel, riskClass, recommendation sql.NullString

		err := rows.Scan(
			&checkID, &orderID, &clientID, &orderAmt, &riskScore,
			&riskLevel, &riskClass, &recommendation, &checkedAt,
		)
		if err != nil {
			return nil, err
		}

		record["check_id"] = checkID
		record["order_id"] = orderID
		record["client_id"] = clientID
		if orderAmt.Valid {
			record["order_amount"] = orderAmt.Float64
		}
		if riskScore.Valid {
			record["risk_score"] = riskScore.Float64
		}
		if riskLevel.Valid {
			record["risk_level"] = riskLevel.String
		}
		if riskClass.Valid {
			record["risk_class"] = riskClass.String
		}
		if recommendation.Valid {
			record["recommendation"] = recommendation.String
		}
		record["checked_at"] = checkedAt.Format("2006-01-02 15:04:05")

		results = append(results, record)
	}

	return results, nil
}

func ClearCheckHistory() error {
	if db == nil {
		return fmt.Errorf("база данных не подключена")
	}

	_, err := db.Exec("TRUNCATE TABLE check_history RESTART IDENTITY")
	return err
}

func CloseDB() {
	if db != nil {
		db.Close()
		log.Println("🔌 Соединение с БД закрыто")
	}
}

func apiGetOrders(w http.ResponseWriter, r *http.Request) {
	defer func() {
		if rec := recover(); rec != nil {
			log.Printf("PANIC in apiGetOrders: %v", rec)
			log.Printf("   Stack: %s", debug.Stack())
			setCORSHeaders(w, r)
			applyJSONHeaders(w)
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"error": "Internal server error",
				"debug": fmt.Sprintf("%v", rec),
			})
		}
	}()

	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	clientID := r.URL.Query().Get("client_id")
	query := r.URL.Query().Get("q")

	log.Printf("🔍 [apiGetOrders] client_id='%s', q='%s', path='%s'", clientID, query, r.URL.Path)

	if clientID == "" {
		log.Printf("⚠️ client_id is empty in request")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "client_id is required"})
		return
	}

	cid, err := strconv.Atoi(clientID)
	if err != nil || cid <= 0 {
		log.Printf("⚠️ Invalid client_id: '%s'", clientID)
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid client_id"})
		return
	}

	orders, err := GetOrdersByClientID(clientID, query)
	if err != nil {
		log.Printf("❌ GetOrdersByClientID returned error: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":     "Database error: " + err.Error(),
			"client_id": clientID,
			"query":     query,
		})
		return
	}

	log.Printf("✅ Found %d orders for client_id=%s", len(orders), clientID)

	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(map[string]interface{}{"orders": orders}); err != nil {
		log.Printf("❌ JSON encode error: %v", err)
		if !strings.Contains(w.Header().Get("Content-Type"), "application/json") {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to encode JSON"})
		}
		return
	}
}

func GetOrdersByClientID(clientID, query string) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("database not connected")
	}

	log.Printf("[DEBUG] GetOrdersByClientID: clientID='%s', query='%s'", clientID, query)

	var querySQL string
	var args []interface{}

	if query != "" {
		querySQL = `
			SELECT o.order_id, o.client_id, o.order_amount, o.items_count,
			       o.payment_method, o.order_timestamp, o.amount_deviation,
			       o.orders_last_30d, o.product_category, 
			       r.days_since_purchase, r.claimed_reason, r.return_channel
			FROM orders o
			LEFT JOIN returns r ON o.order_id = r.order_id
			WHERE o.client_id = $1 AND o.order_id::TEXT LIKE $2
			ORDER BY o.order_timestamp DESC
			LIMIT 20
		`
		args = []interface{}{clientID, "%" + query + "%"}
	} else {
		querySQL = `
			SELECT o.order_id, o.client_id, o.order_amount, o.items_count,
			       o.payment_method, o.order_timestamp, o.amount_deviation,
			       o.orders_last_30d, o.product_category, 
			       r.days_since_purchase, r.claimed_reason, r.return_channel
			FROM orders o
			LEFT JOIN returns r ON o.order_id = r.order_id
			WHERE o.client_id = $1
			ORDER BY o.order_timestamp DESC
			LIMIT 20
		`
		args = []interface{}{clientID}
	}

	log.Printf("[DEBUG] Executing query with %d args", len(args))

	rows, err := db.Query(querySQL, args...)
	if err != nil {
		log.Printf("❌ Query execution error: %v | SQL: %s", err, querySQL)
		return nil, fmt.Errorf("query failed: %w", err)
	}
	defer rows.Close()

	var results []DBRecord
	rowNum := 0

	for rows.Next() {
		rowNum++
		var record DBRecord = make(DBRecord)
		var ts time.Time

		var orderID, clientID, itemsCount int
		var orderAmount, amountDev sql.NullFloat64
		var paymentMethod, prodCategory, claimedReason, returnChannel sql.NullString
		var daysSincePurchase, ordersLast30d sql.NullInt64

		err := rows.Scan(
			&orderID, &clientID, &orderAmount, &itemsCount,
			&paymentMethod, &ts, &amountDev,
			&ordersLast30d,
			&prodCategory, &daysSincePurchase, &claimedReason, &returnChannel,
		)
		if err != nil {
			log.Printf("Scan error on row #%d: %v", rowNum, err)
			log.Printf("   Column types: order_id=int, client_id=int, order_amount=float, items_count=int, payment_method=string, order_timestamp=timestamp, amount_deviation=float, orders_last_30d=int, product_category=string, days_since_purchase=int, claimed_reason=string, return_channel=string")
			return nil, fmt.Errorf("scan failed on row %d: %w", rowNum, err)
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
		if ordersLast30d.Valid {
			record["orders_last_30d"] = ordersLast30d.Int64
		}
		if prodCategory.Valid {
			record["product_category"] = prodCategory.String
		}
		if daysSincePurchase.Valid {
			record["days_to_return"] = daysSincePurchase.Int64
		}
		if claimedReason.Valid {
			record["reason"] = claimedReason.String
		}
		if returnChannel.Valid {
			record["return_channel"] = returnChannel.String
		}

		results = append(results, record)
	}

	if err = rows.Err(); err != nil {
		log.Printf("❌ rows iteration error: %v", err)
		return nil, err
	}

	log.Printf("[DEBUG] GetOrdersByClientID: successfully processed %d rows", rowNum)
	return results, nil
}

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

func GetUsersList(page, limit int) ([]UserCard, int, error) {
	if db == nil {
		return nil, 0, fmt.Errorf("база данных не подключена")
	}

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

func GetUserRiskStats() (activeCount, warningCount, highRiskCount int, err error) {
	if db == nil {
		return 0, 0, 0, fmt.Errorf("база данных не подключена")
	}

	query := `
                SELECT
                        COUNT(CASE WHEN global_return_rate IS NOT NULL AND global_return_rate <= 0.2 THEN 1 END) as active,
                        COUNT(CASE WHEN global_return_rate IS NOT NULL AND global_return_rate > 0.2 AND global_return_rate <= 0.5 THEN 1 END) as warning,
                        COUNT(CASE WHEN global_return_rate IS NOT NULL AND global_return_rate > 0.5 THEN 1 END) as high
                FROM clients
        `

	err = db.QueryRow(query).Scan(&activeCount, &warningCount, &highRiskCount)
	if err != nil {
		return 0, 0, 0, err
	}

	return activeCount, warningCount, highRiskCount, nil
}

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
		var ts, createdAt time.Time

		var orderID int
		var orderAmount sql.NullFloat64
		var itemsCount int
		var paymentMethod sql.NullString
		var amountDev sql.NullFloat64
		var orderStatus, productCategory sql.NullString

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

func GetUserReturns(clientID, limit int) ([]DBRecord, error) {
	if db == nil {
		return nil, fmt.Errorf("база данных не подключена")
	}

	query := `
		SELECT return_id, order_id, days_since_purchase, return_channel,
		       has_receipt, tags_removed, missing_components, comment, created_at
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

		var returnID, orderID, daysSincePurchase int
		var returnChannel, comment sql.NullString
		var hasReceipt, tagsRemoved, missingComponents bool

		err := rows.Scan(
			&returnID, &orderID, &daysSincePurchase,
			&returnChannel, &hasReceipt,
			&tagsRemoved, &missingComponents, &comment, &ts,
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
		if comment.Valid {
			record["comment"] = comment.String
		}
		record["created_at"] = ts.Format("02.01.2006 15:04")

		returns = append(returns, record)
	}
	return returns, nil
}

func apiGetClient(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/client/")
	clientID, err := strconv.Atoi(path)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid client ID"})
		return
	}

	client, err := GetClientByID(clientID)
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Client not found"})
		return
	}

	json.NewEncoder(w).Encode(client)
}

func apiGetOrder(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/order/")
	orderID, err := strconv.Atoi(path)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid order ID"})
		return
	}

	order, err := GetOrderByID(orderID)
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Order not found"})
		return
	}

	json.NewEncoder(w).Encode(order)
}

func apiGetStats(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	stats, err := GetStats()
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to get stats"})
		return
	}

	json.NewEncoder(w).Encode(stats)
}

func apiGetUsers(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
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
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to fetch users"})
		return
	}

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

func apiGetUserDetail(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/users/")
	clientID, err := strconv.Atoi(path)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid user ID"})
		return
	}

	client, err := GetClientByID(clientID)
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "User not found"})
		return
	}

	orders, _ := GetUserOrders(clientID, 5)
	returns, _ := GetUserReturns(clientID, 5)

	json.NewEncoder(w).Encode(map[string]interface{}{
		"client":         client,
		"recent_orders":  orders,
		"recent_returns": returns,
	})
}

func apiSearchUsers(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	query := r.URL.Query().Get("q")
	if query == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Search query is required"})
		return
	}

	limit := 10
	if l := r.URL.Query().Get("limit"); l != "" {
		limit, _ = strconv.Atoi(l)
	}

	if id, err := strconv.Atoi(query); err == nil {
		user, err := GetClientByID(id)
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "User not found"})
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

	w.WriteHeader(http.StatusNotFound)
	json.NewEncoder(w).Encode(map[string]interface{}{"error": "User not found"})
}

func apiGetHistory(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		limit, _ = strconv.Atoi(l)
	}

	checks, err := GetAllCheckHistory(limit)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to fetch history"})
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{"checks": checks})
}

func apiClearHistory(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	if err := ClearCheckHistory(); err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to clear history"})
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{"status": "ok"})
}

func apiClientReturnsHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	switch r.Method {
	case http.MethodGet:
		apiGetClientReturns(w, r)
	case http.MethodPost:
		apiCreateClientReturn(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
	}
}

func apiGetClientReturns(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	clientID, err := getClientIDFromRequest(r)
	if err != nil {
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Unauthorized: " + err.Error()})
		return
	}

	returns, err := GetUserReturns(clientID, 100)
	if err != nil {
		log.Printf("❌ Ошибка получения возвратов: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to fetch returns"})
		return
	}

	if returns == nil {
		returns = []DBRecord{}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "returns": returns})
}

func apiCreateClientReturn(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	clientID, err := getClientIDFromRequest(r)
	if err != nil {
		log.Printf("❌ Auth error: %v", err)
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "message": "Unauthorized"})
		return
	}

	bodyBytes, err := io.ReadAll(r.Body)
	if err != nil {
		log.Printf("❌ Ошибка чтения тела: %v", err)
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "message": "Cannot read request body"})
		return
	}
	r.Body = io.NopCloser(bytes.NewBuffer(bodyBytes))

	var req CreateReturnRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		log.Printf("❌ Ошибка парсинга JSON: %v", err)
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "message": "Invalid JSON format"})
		return
	}

	if req.OrderID <= 0 {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success": false,
			"message": "Order ID должен быть положительным числом",
		})
		return
	}

	if req.ClaimedReason == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success": false,
			"message": "Причина возврата обязательна",
		})
		return
	}

	orderQuery := `SELECT order_id FROM orders WHERE order_id = $1 AND client_id = $2 LIMIT 1`
	var orderID int
	err = db.QueryRow(orderQuery, req.OrderID, clientID).Scan(&orderID)
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success": false,
			"message": "Заказ не найден или не принадлежит вам",
		})
		return
	}

	insertQuery := `INSERT INTO returns (
		order_id, client_id, days_since_purchase,
		has_receipt, tags_removed, missing_components,
		return_channel, claimed_reason, created_at
	) VALUES ($1, $2, 0, true, false, false, 'online', $3, NOW())
	RETURNING return_id`

	var returnID int
	err = db.QueryRow(insertQuery, req.OrderID, clientID, req.ClaimedReason).Scan(&returnID)
	if err != nil {
		log.Printf("❌ Ошибка создания возврата: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success": false,
			"message": "Ошибка создания возврата: " + err.Error(),
		})
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"success":   true,
		"return_id": returnID,
		"message":   "Возврат успешно создан",
	})
}

func loginPage(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("templates/login.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func clientProfileHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	tmpl, err := template.ParseFiles("templates/client_profile.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func clientOrdersHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	tmpl, err := template.ParseFiles("templates/client_orders.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func clientReturnsHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	tmpl, err := template.ParseFiles("templates/client_returns.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func clientChatHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	tmpl, err := template.ParseFiles("templates/client_chat.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func adminChatHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	tmpl, err := template.ParseFiles("templates/admin_chat.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func adminProfileHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	stats, err := GetStats()
	if err != nil {
		stats = map[string]interface{}{"total_clients": 0, "total_orders": 0, "total_returns": 0, "high_risk": 0}
	}

	data := map[string]interface{}{"Stats": stats, "DBConnected": db != nil}

	tmpl, err := template.ParseFiles("templates/admin_profile.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, data)
}

func homePage(w http.ResponseWriter, r *http.Request) {
	stats, err := GetStats()
	if err != nil {
		stats = map[string]interface{}{"total_clients": 0, "total_orders": 0, "total_returns": 0, "high_risk": 0}
	}
	data := map[string]interface{}{"Stats": stats, "DBConnected": db != nil}

	tmpl, err := template.ParseFiles("templates/index.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, data)
}

func checkHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
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
		var form FormData

		contentType := r.Header.Get("Content-Type")
		if strings.Contains(contentType, "application/json") {
			if err := json.NewDecoder(r.Body).Decode(&form); err != nil {
				http.Error(w, "Invalid JSON: "+err.Error(), http.StatusBadRequest)
				return
			}
		} else {
			r.ParseForm()
			form = FormData{
				ClientID: parseInt(r.FormValue("clientID")), OrderID: parseInt(r.FormValue("orderID")),
				Category: r.FormValue("category"), ClaimedReason: r.FormValue("reason"),
				AddressMatch: r.FormValue("addressMatch") == "on", DeviceNew: r.FormValue("deviceNew") == "on",
				IsWeekend: parseBool(r.FormValue("isWeekend")), HasTag: parseBool(r.FormValue("hasTag")),
				HasReceipt: parseBool(r.FormValue("hasReceipt")), HasDamage: parseBool(r.FormValue("hasDamage")),
				IsUsed: parseBool(r.FormValue("isUsed")), TagsRemoved: r.FormValue("tagsRemoved") == "on",
				MissingComponents: r.FormValue("missingComponents") == "on",
				DaysToReturn:      parseInt(r.FormValue("daysToReturn")),
			}
		}

		if form.OrderAmount <= 0 {
			form.OrderAmount = parseFloat(r.FormValue("orderAmount"))
		}
		if form.AccountAgeDays <= 0 {
			form.AccountAgeDays = parseInt(r.FormValue("accountAgeDays"))
		}
		if form.TotalOrders <= 0 {
			form.TotalOrders = parseInt(r.FormValue("totalOrders"))
		}

		if err := EnrichFromDB(&form); err != nil {
			log.Printf("[WARN] %v", err)
		}

		if form.OrderID > 0 && form.ClientID > 0 {
			SaveReturnToDB(form)
		}

		result := calculateRisk(form)
		if result.OrderID > 0 && result.ClientID > 0 {
			SaveCheckHistory(result)
		}

		applyJSONHeaders(w)
		json.NewEncoder(w).Encode(result)
		return
	}

	http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
}

func historyPage(w http.ResponseWriter, r *http.Request) {
	checks, err := GetAllCheckHistory(50)
	if err != nil {
		checks = []DBRecord{}
	}
	data := map[string]interface{}{"Checks": checks, "UseDatabase": db != nil && len(checks) > 0, "DBConnected": db != nil}

	tmpl, err := template.ParseFiles("templates/history.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, data)
}

func usersPage(w http.ResponseWriter, r *http.Request) {
	pageStr := r.URL.Query().Get("page")
	page := 1
	if pageStr != "" {
		page, _ = strconv.Atoi(pageStr)
		if page < 1 {
			page = 1
		}
	}
	limit := 20

	users, total, err := GetUsersList(page, limit)
	if err != nil {
		users = []UserCard{}
	}

	activeCount, warningCount, highRiskCount, _ := GetUserRiskStats()

	data := map[string]interface{}{
		"Users": users, "Total": total, "ActiveCount": activeCount,
		"WarningCount": warningCount, "HighRiskCount": highRiskCount,
		"Page": page, "Limit": limit, "TotalPages": (total + limit - 1) / limit,
	}

	funcMap := template.FuncMap{"sub": func(a, b int) int { return a - b }, "add": func(a, b int) int { return a + b }, "div": func(a, b int) int { return a / b }}
	tmpl, err := template.New("users.html").Funcs(funcMap).ParseFiles("templates/users.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, data)
}

func calculateRisk(f FormData) ResultData {
	log.Printf("[DEBUG] FormData: %+v", f)

	score, mlFeatures, err := predictRiskWithFeatures(f)

	if err != nil {
		log.Printf("⚠️ Python ONNX ошибка: %v, используем заглушку", err)
		score = calculateRiskFallback(f)
		mlFeatures = []FeatureExplanation{}
	}

	if score < 0 {
		score = 0
	}
	if score > 1 {
		score = 1
	}

	if db != nil && f.ClientID > 0 {
		enrichedScore, _ := enrichRiskFromDB(f.ClientID, float64(score))
		score = float32(enrichedScore)
	}

	level, class, recommendation := getRiskLevel(score)

	normalizedFeatures := normalizeContributions(mlFeatures)
	factors := getRiskFactors(f, normalizedFeatures)

	log.Printf("[INFO] Final score: %.4f, Level: %s, Factors: %v", score, level, factors)

	return ResultData{
		FormData:         f,
		RiskScore:        float64(score),
		RiskLevel:        level,
		RiskClass:        class,
		Recommendation:   recommendation,
		TopFactors:       factors,
		TopFeatures:      normalizedFeatures,
		StrokeDashOffset: (1 - float64(score)) * 283,
		RiskPercent:      int(float64(score) * 100),
		OrderID:          f.OrderID,
		ClientID:         f.ClientID,
	}
}

func normalizeContributions(features []FeatureExplanation) []FeatureExplanation {
	if len(features) == 0 {
		return features
	}

	type scoredFeat struct {
		FeatureExplanation
		absContribution float64
	}

	scored := make([]scoredFeat, 0, len(features))
	for _, f := range features {
		if f.Effect == "повышает риск" && f.Contribution > 0.001 {
			scored = append(scored, scoredFeat{
				FeatureExplanation: f,
				absContribution:    math.Abs(f.Contribution),
			})
		}
	}

	if len(scored) == 0 {
		return []FeatureExplanation{}
	}

	sort.Slice(scored, func(i, j int) bool {
		return scored[i].absContribution > scored[j].absContribution
	})

	visualWeights := []int{25, 20, 15, 12, 10, 8, 6, 5, 4, 3}

	result := make([]FeatureExplanation, 0, len(scored))
	for i, sf := range scored {
		if i >= len(visualWeights) {
			break
		}
		feat := sf.FeatureExplanation
		feat.Contribution = float64(visualWeights[i]) / 100.0
		result = append(result, feat)
	}

	return result
}

func getRiskFactors(f FormData, mlFeatures []FeatureExplanation) []string {
	factors := []string{}
	seen := make(map[string]bool)

	mlMarkers := []string{"[!!!]", "[!!] ", "[!]  ", "[.]  "}

	mlCount := 0
	for _, feat := range mlFeatures {
		if mlCount >= 4 {
			break
		}
		if feat.Effect == "повышает риск" && feat.Contribution > 0.03 {
			label := getFeatureLabel(feat.Feature)
			if !seen[label] {
				pct := int(feat.Contribution * 100)
				idx := 3
				if pct >= 20 {
					idx = 0
				} else if pct >= 15 {
					idx = 1
				} else if pct >= 10 {
					idx = 2
				}
				factors = append(factors, fmt.Sprintf("%s %s", mlMarkers[idx], label))
				seen[label] = true
				mlCount++
			}
		}
	}

	if mlCount < 4 {
		ruleFactors := getRuleBasedFactors(f)
		for _, factor := range ruleFactors {
			if len(factors) >= 7 {
				break
			}

			clean := factor
			if idx := strings.Index(factor, " ("); idx != -1 {
				clean = factor[:idx]
			}
			if !seen[clean] {
				factors = append(factors, "[~] "+clean)
				seen[clean] = true
			}
		}
	}

	if len(factors) == 0 {
		factors = append(factors, "Все параметры в норме")
	}
	if len(factors) > 7 {
		factors = factors[:7]
	}

	return factors
}

func getRuleBasedFactors(f FormData) []string {
	factors := []string{}

	if !f.HasTag {
		factors = append(factors, "Бирка отсутствует (+25%)")
	}
	if !f.HasReceipt {
		factors = append(factors, "Чек не предоставлен (+15%)")
	}
	if f.HasDamage {
		factors = append(factors, "Есть повреждения товара (+20%)")
	}
	if f.IsUsed {
		factors = append(factors, "Товар имеет следы использования (+25%)")
	}
	if f.ClaimedReason == "changed_mind" {
		factors = append(factors, "Возврат без объективной причины (+15%)")
	}
	if f.DaysToReturn <= 3 {
		factors = append(factors, "Очень быстрый возврат (+10%)")
	}
	if f.AccountAgeDays < 30 {
		factors = append(factors, "🆕 Новый аккаунт (+12%)")
	}
	if f.ReturnRate > 30 {
		factors = append(factors, "Высокий % возвратов у клиента (+18%)")
	}
	if f.OrderAmount > 30000 {
		factors = append(factors, "Высокая сумма заказа (+10%)")
	}

	if f.PaymentMethodRisk > 0.5 {
		factors = append(factors, "Рискованный метод оплаты (+10%)")
	}

	if f.ShippingRegionRisk > 0.5 {
		factors = append(factors, "Рискованный регион доставки (+8%)")
	}

	if f.DistanceFromRegistration > 500 {
		factors = append(factors, "Большое расстояние от регистрации (+7%)")
	}

	if f.DeviceTrustScore < 0.5 {
		factors = append(factors, "Низкое доверие к устройству (+10%)")
	}

	if f.IPTrustScore < 0.5 {
		factors = append(factors, "Низкое доверие к IP (+10%)")
	}
	if f.TagsRemoved {
		factors = append(factors, "Бирки удалены (+20%)")
	}
	if f.MissingComponents {
		factors = append(factors, "Отсутствуют компоненты (+18%)")
	}

	sort.Slice(factors, func(i, j int) bool {
		extractPct := func(s string) int {
			start := strings.Index(s, "(+")
			if start == -1 {
				return 0
			}
			end := strings.Index(s[start:], "%)")
			if end == -1 {
				return 0
			}
			pct, _ := strconv.Atoi(s[start+2 : start+end])
			return pct
		}
		return extractPct(factors[i]) > extractPct(factors[j])
	})

	return factors
}

func enrichRiskFromDB(clientID int, baseScore float64) (float64, []string) {
	extraFactors := []string{}
	score := baseScore

	client, err := GetClientByID(clientID)
	if err != nil {
		return score, extraFactors
	}

	if rate, ok := (*client)["global_return_rate"].(float64); ok && rate > 0.3 {
		score += 0.15
		extraFactors = append(extraFactors, "Высокий % возвратов у клиента")
	}

	if age, ok := (*client)["account_age_days"].(int); ok && age < 30 {
		score += 0.10
		extraFactors = append(extraFactors, "Новый аккаунт")
	}

	if total, ok := (*client)["total_returns"].(int); ok && total > 10 {
		score += 0.08
		extraFactors = append(extraFactors, "Много возвратов в истории")
	}

	if freq, ok := (*client)["address_change_frequency"].(float64); ok && freq > 2.0 {
		score += 0.07
		extraFactors = append(extraFactors, "Частая смена адресов")
	}

	if score > 1.0 {
		score = 1.0
	}
	if score < 0.0 {
		score = 0.0
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
	features[0] = float32(f.AccountAgeDays) / 730.0
	features[1] = float32(f.TotalOrders) / 100.0
	features[2] = float32(f.TotalReturns) / 50.0
	features[3] = float32(f.ReturnRate) / 100.0
	features[4] = float32(f.OrderAmount) / 200000.0

	catMap := map[string]float32{"electronics": 0, "clothing": 1, "cosmetics": 2, "books": 3, "sports": 4, "home": 5}
	features[5] = catMap[f.Category]

	features[6] = b2f(f.OrderAmount > 30000)
	features[7] = b2f(f.IsWeekend)
	features[8] = b2f(f.AddressMatch)
	features[9] = b2f(f.DeviceNew)
	features[10] = b2f(f.HasReceipt)

	reasonMap := map[string]float32{"defect": 2, "size": 0, "color": 1, "quality": 2, "changed_mind": 1, "other": 14}
	features[11] = reasonMap[f.ClaimedReason]

	features[12] = float32(f.DiscountPercent) / 50.0
	features[13] = b2f(f.PromoCodeUsed)
	features[14] = b2f(f.FirstOrderDiscountAbuse)
	features[15] = b2f(f.Category == "electronics")
	features[16] = float32(f.ItemsInOrder) / 10.0

	features[17] = float32(f.PaymentMethodRisk)
	features[18] = b2f(f.ChargebackHistory90d)
	features[19] = b2f(f.CardBinCountryMismatch)
	features[20] = float32(f.ShippingRegionRisk)
	features[21] = float32(f.DeliveryAddressType) / 2.0
	features[22] = float32(f.DistanceFromRegistration) / 2000.0

	features[23] = float32(f.OrderHour) / 23.0
	features[24] = b2f(f.OrderTimeNight)

	features[25] = float32(f.IPVelocity24h) / 20.0
	features[26] = float32(f.IPVelocity7d) / 50.0
	features[27] = float32(f.AccountsPerIP) / 10.0
	features[28] = float32(f.AccountsPerPhone) / 10.0
	features[29] = float32(f.AccountsPerDevice) / 10.0

	features[30] = b2f(f.DeviceIsEmulator)
	features[31] = float32(f.DeviceTrustScore)
	features[32] = float32(f.IPTrustScore)

	features[33] = float32(f.AvgOrderAmount) / 200000.0
	features[34] = float32(f.ReturnRate30d)

	features[35] = float32(f.RefundVelocity7d) / 10.0
	features[36] = float32(f.RefundVelocity30d) / 20.0
	features[37] = float32(f.SupportTickets30d) / 10.0
	features[38] = float32(f.ReviewCount30d) / 20.0

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
	if f.ClaimedReason == "changed_mind" {
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
	if score > 1.0 {
		score = 1.0
	}
	if score < 0.0 {
		score = 0.0
	}
	return float32(score)
}

var fastAPIURL = "http://localhost:8000"

func callFastAPI(endpoint string, payload interface{}, result interface{}) error {
	url := fastAPIURL + endpoint
	jsonData, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("ошибка маршалинга JSON: %v", err)
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Post(url, "application/json", bytes.NewBuffer(jsonData))
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

type FastAPIFraudPredictionResponse struct {
	Success          bool                 `json:"success"`
	Score            float64              `json:"score,omitempty"`
	Error            string               `json:"error,omitempty"`
	RiskLevel        string               `json:"risk_level,omitempty"`
	Recommendation   string               `json:"recommendation,omitempty"`
	ReturnID         int                  `json:"return_id,omitempty"`
	ClientID         int                  `json:"client_id,omitempty"`
	OrderID          int                  `json:"order_id,omitempty"`
	ProbabilityFraud float64              `json:"probability_fraud,omitempty"`
	AnomalyScore     float64              `json:"anomaly_score,omitempty"`
	IsAnomaly        bool                 `json:"is_anomaly,omitempty"`
	CombinedScore    float64              `json:"combined_score,omitempty"`
	Decision         string               `json:"decision,omitempty"`
	TopFeatures      []FeatureExplanation `json:"top_features,omitempty"`
}

type FastAPIChatRequest struct {
	Message string `json:"message"`
}

type FastAPIChatResponse struct {
	Response string `json:"response"`
	Error    string `json:"error,omitempty"`
}

func loadModel(modelPath string) error {
	req := FastAPILoadModelRequest{ModelPath: modelPath, ModelType: "fraud"}
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

func predictRiskWithFeatures(f FormData) (float32, []FeatureExplanation, error) {
	payload := FastAPIFraudPayloadRequest{
		ClientID: f.ClientID, OrderID: f.OrderID, ReturnID: 0,
		AccountAgeDays: f.AccountAgeDays, TotalOrders: f.TotalOrders,
		TotalReturns: f.TotalReturns, GlobalReturnRate: f.ReturnRate,
		AvgOrderAmount: f.AvgOrderAmount, OrderAmount: f.OrderAmount,
		ItemsCount:     f.ItemsInOrder,
		DiscountAmount: f.OrderAmount * f.DiscountPercent / 100.0,
		PaymentMethod:  "card", AmountDeviation: 0,
		OrdersLast30d: int(f.RefundVelocity30d), ProductCategory: f.Category,
		IsElectronics: f.IsElectronics, ShippingRegion: "Moscow",
		RegionRiskScore: f.ShippingRegionRisk, DeliveryCity: "Moscow",
		DistanceFromRegKm:   f.DistanceFromRegistration,
		CardCountryMismatch: f.CardBinCountryMismatch,
		DeliveryAddressType: "home", AddressMatchScore: 1.0,
		IsAddressMatch: f.AddressMatch, ReturnsLast30d: int(f.RefundVelocity30d),
		ReturnRateLast30d: f.ReturnRate30d, DaysSinceLastReturn: 999,
		DaysSincePurchase: f.DaysSincePurchase, ReturnChannel: f.ReturnChannel,
		HasReceipt: f.HasReceipt, TagsRemoved: f.TagsRemoved,
		MissingComponents: f.MissingComponents, ClaimedReason: f.ClaimedReason,
	}

	var resp FastAPIFraudPredictionResponse
	err := callFastAPI("/api/predict-fraud-payload", payload, &resp)
	if err != nil {
		log.Printf("[ERROR] FastAPI predict error: %v", err)
		return 0, []FeatureExplanation{}, err
	}
	if !resp.Success {
		return 0, []FeatureExplanation{}, fmt.Errorf("%s", resp.Error)
	}

	score := resp.CombinedScore
	if score == 0 {
		score = resp.ProbabilityFraud
	}

	log.Printf("[INFO] v4 prediction: combined=%.4f, prob=%.4f, decision=%s, features=%d",
		resp.CombinedScore, resp.ProbabilityFraud, resp.Decision, len(resp.TopFeatures))

	return float32(score), resp.TopFeatures, nil
}

func predictRisk(f FormData) (float32, error) {
	score, _, err := predictRiskWithFeatures(f)
	return score, err
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

func parseBool(value string) bool {
	return value == "1" || value == "true" || value == "on" || value == "yes"
}

func handleChat(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

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
		Message string `json:"message"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid request body"})
		return
	}

	if req.Message == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Message is required"})
		return
	}

	response, err := callPythonModelForChat(req.Message)
	if err != nil {
		log.Printf("[ERROR] Chat model error: %v", err)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"response": "Извините, я пока учусь и не могу ответить на этот вопрос. Попробуйте спросить что-то другое!",
		})
		return
	}

	json.NewEncoder(w).Encode(map[string]interface{}{"response": response})
}

func callPythonModelForChat(message string) (string, error) {
	req := FastAPIChatRequest{Message: message}
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
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

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

	err = bcrypt.CompareHashAndPassword([]byte(passwordHash), []byte(req.Password))
	if err != nil {
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Неверный логин или пароль"})
		return
	}

	userData := map[string]interface{}{"id": foundUser.ID, "login": foundUser.Login, "role": foundUser.Role}
	userJSON, _ := json.Marshal(userData)
	encodedUser := base64.StdEncoding.EncodeToString(userJSON)

	http.SetCookie(w, &http.Cookie{
		Name: "user", Value: encodedUser, Path: "/", MaxAge: 86400,
		HttpOnly: false, SameSite: http.SameSiteLaxMode,
	})

	json.NewEncoder(w).Encode(map[string]interface{}{"user": userData})
}

func apiLogout(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	http.SetCookie(w, &http.Cookie{
		Name: "user", Value: "", Path: "/", MaxAge: -1,
		HttpOnly: false, SameSite: http.SameSiteLaxMode,
	})

	json.NewEncoder(w).Encode(map[string]interface{}{"success": true})
}

func apiGetClientOrders(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	clientIDStr := r.URL.Query().Get("client_id")

	if clientIDStr == "" {
		clientID, err := getClientIDFromRequest(r)
		if err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Unauthorized: " + err.Error()})
			return
		}
		clientIDStr = fmt.Sprintf("%d", clientID)
	}

	clientID, err := strconv.Atoi(clientIDStr)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid client ID"})
		return
	}

	orders, err := GetUserOrders(clientID, 100)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Failed to fetch orders: " + err.Error()})
		return
	}

	if orders == nil {
		orders = []DBRecord{}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{"orders": orders})
}

func EnrichFromDB(f *FormData) error {
	if db == nil || f.ClientID <= 0 || f.OrderID <= 0 {
		return fmt.Errorf("БД не подключена или не указаны ClientID/OrderID")
	}

	clientQuery := `SELECT account_age_days, total_orders, total_returns, global_return_rate, avg_order_amount, address_change_frequency FROM clients WHERE client_id = $1`
	var accountAge, totalOrders, totalReturns int
	var globalRate, avgOrderAmt, addrFreq sql.NullFloat64

	err := db.QueryRow(clientQuery, f.ClientID).Scan(&accountAge, &totalOrders, &totalReturns, &globalRate, &avgOrderAmt, &addrFreq)
	if err == nil {
		f.AccountAgeDays = accountAge
		f.TotalOrders = totalOrders
		if totalOrders > 0 {
			f.ReturnRate = globalRate.Float64 * 100
		}
		if avgOrderAmt.Valid {
			f.AvgOrderAmount = avgOrderAmt.Float64
		}
	}

	orderQuery := `SELECT order_amount, items_count, discount_amount, payment_method, order_timestamp FROM orders WHERE order_id = $1 AND client_id = $2`
	var orderAmt, discAmt sql.NullFloat64
	var itemsCount int
	var payMethod sql.NullString
	var orderTS time.Time

	err = db.QueryRow(orderQuery, f.OrderID, f.ClientID).Scan(&orderAmt, &itemsCount, &discAmt, &payMethod, &orderTS)
	if err == nil {
		if orderAmt.Valid {
			f.OrderAmount = orderAmt.Float64
		}
		f.ItemsInOrder = itemsCount
		if discAmt.Valid && f.OrderAmount > 0 {
			f.DiscountPercent = (discAmt.Float64 / f.OrderAmount) * 100
		}
		f.OrderHour = orderTS.Hour()
		f.IsWeekend = orderTS.Weekday() == time.Saturday || orderTS.Weekday() == time.Sunday
		if f.OrderHour >= 0 && f.OrderHour <= 5 {
			f.OrderTimeNight = true
		}
	}

	velocityQuery := `SELECT COUNT(*) FROM returns WHERE client_id = $1 AND created_at > NOW() - INTERVAL '30 days'`
	var refundVelocity30d int
	db.QueryRow(velocityQuery, f.ClientID).Scan(&refundVelocity30d)
	f.RefundVelocity30d = refundVelocity30d

	ipVelocityQuery := `SELECT COUNT(DISTINCT o2.order_id) FROM orders o2 WHERE o2.client_id = $1 AND o2.order_timestamp > NOW() - INTERVAL '24 hours'`
	var ipVel24h int
	db.QueryRow(ipVelocityQuery, f.ClientID).Scan(&ipVel24h)
	f.IPVelocity24h = ipVel24h

	ipVelocity7dQuery := `SELECT COUNT(DISTINCT o2.order_id) FROM orders o2 WHERE o2.client_id = $1 AND o2.order_timestamp > NOW() - INTERVAL '7 days'`
	var ipVel7d int
	db.QueryRow(ipVelocity7dQuery, f.ClientID).Scan(&ipVel7d)
	f.IPVelocity7d = ipVel7d

	ticketsQuery := `SELECT COUNT(*) FROM returns WHERE client_id = $1 AND created_at > NOW() - INTERVAL '30 days' AND (tags_removed = true OR missing_components = true)`
	var supportTickets int
	db.QueryRow(ticketsQuery, f.ClientID).Scan(&supportTickets)
	f.SupportTickets30d = supportTickets

	if addrFreq.Valid {
		f.AccountsPerIP = int(addrFreq.Float64) + 1
	} else {
		f.AccountsPerIP = 1
	}
	f.AccountsPerDevice = 1
	f.AccountsPerPhone = 1

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

	if addrFreq.Valid && addrFreq.Float64 > 2.0 {
		f.ShippingRegionRisk = 0.4
	} else {
		f.ShippingRegionRisk = 0.2
	}

	if addrFreq.Valid {
		f.DistanceFromRegistration = addrFreq.Float64 * 100
	} else {
		f.DistanceFromRegistration = 50
	}

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

	f.PromoCodeUsed = (f.DiscountPercent > 10)
	if f.TotalOrders == 1 && f.DiscountPercent > 20 {
		f.FirstOrderDiscountAbuse = true
	}
	f.ReviewCount30d = f.TotalOrders / 3
	if f.TotalOrders > 0 {
		f.ReturnRate30d = float64(refundVelocity30d) / float64(f.TotalOrders)
	}

	log.Printf("[DEBUG] DB-Enriched: AccountAge=%d, Orders=%d, Returns=%d, Velocity30d=%d, ReturnRate=%.2f%%",
		f.AccountAgeDays, f.TotalOrders, f.TotalReturns, refundVelocity30d, f.ReturnRate)

	return nil
}

func apiPredictFraudV4(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

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
		ReturnID int `json:"return_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "error": "Invalid request body"})
		return
	}

	if req.ReturnID <= 0 {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "error": "return_id is required"})
		return
	}

	log.Printf("🔮 Fraud prediction requested for return_id=%d", req.ReturnID)

	returnQuery := `
		SELECT r.return_id, r.order_id, r.client_id, r.claimed_reason, r.days_since_purchase,
		       r.has_receipt, r.tags_removed, r.missing_components, r.return_channel,
		       o.order_amount, o.items_count, o.payment_method, o.product_category,
		       c.account_age_days, c.total_orders, c.total_returns, c.global_return_rate, c.avg_order_amount
		FROM returns r
		JOIN orders o ON r.order_id = o.order_id
		JOIN clients c ON r.client_id = c.client_id
		WHERE r.return_id = $1
	`

	var (
		returnID, orderID, clientID, daysSincePurchase, itemsCount   int
		claimedReason, returnChannel, productCategory, paymentMethod sql.NullString
		hasReceipt, tagsRemoved, missingComponents                   bool
		orderAmount, globalReturnRate, avgOrderAmount                sql.NullFloat64
		accountAgeDays, totalOrders, totalReturns                    int
	)

	err := db.QueryRow(returnQuery, req.ReturnID).Scan(
		&returnID, &orderID, &clientID, &claimedReason, &daysSincePurchase,
		&hasReceipt, &tagsRemoved, &missingComponents, &returnChannel,
		&orderAmount, &itemsCount, &paymentMethod, &productCategory,
		&accountAgeDays, &totalOrders, &totalReturns, &globalReturnRate, &avgOrderAmount,
	)
	if err != nil {
		log.Printf("❌ Return not found: return_id=%d, error: %v", req.ReturnID, err)
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "error": "Return not found"})
		return
	}

	fraudPayload := map[string]interface{}{
		"return_id":                     returnID,
		"order_id":                      orderID,
		"client_id":                     clientID,
		"claimed_reason":                claimedReason.String,
		"days_since_purchase":           daysSincePurchase,
		"has_receipt":                   hasReceipt,
		"tags_removed":                  tagsRemoved,
		"missing_components":            missingComponents,
		"return_channel":                returnChannel.String,
		"order_amount":                  orderAmount.Float64,
		"items_count":                   itemsCount,
		"payment_method":                paymentMethod.String,
		"product_category":              productCategory.String,
		"account_age_days":              accountAgeDays,
		"total_orders":                  totalOrders,
		"total_returns":                 totalReturns,
		"global_return_rate":            globalReturnRate.Float64,
		"avg_order_amount":              avgOrderAmount.Float64,
		"is_electronics":                productCategory.String == "electronics",
		"region_risk_score":             0.2,
		"distance_from_registration_km": 50.0,
		"card_country_mismatch":         false,
		"address_match_score":           1.0,
		"is_address_match":              true,
		"returns_last_30d":              0,
		"return_rate_last_30d":          0.0,
		"days_since_last_return":        999,
	}

	fastAPIURL := "http://localhost:8000/api/predict-fraud-payload"
	jsonData, _ := json.Marshal(fraudPayload)

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Post(fastAPIURL, "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		log.Printf("⚠️ FastAPI unavailable, using fallback: %v", err)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success":           true,
			"return_id":         returnID,
			"probability_fraud": 0.25,
			"anomaly_score":     0.18,
			"is_anomaly":        false,
			"combined_score":    0.25,
			"decision":          "approve",
			"risk_level":        "low",
			"recommendation":    "Возврат можно одобрить",
		})
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("❌ Error reading FastAPI response: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "error": "Failed to read prediction"})
		return
	}

	if resp.StatusCode != http.StatusOK {
		log.Printf("⚠️ FastAPI returned status %d: %s", resp.StatusCode, string(body))
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success":           true,
			"return_id":         returnID,
			"probability_fraud": 0.30,
			"anomaly_score":     0.20,
			"is_anomaly":        false,
			"combined_score":    0.30,
			"decision":          "review",
			"risk_level":        "medium",
			"recommendation":    "Требуется дополнительная проверка",
		})
		return
	}

	w.WriteHeader(http.StatusOK)
	w.Write(body)
}

func apiHandleDecisionAlias(w http.ResponseWriter, r *http.Request) {
	apiHandleDecision(w, r)
}

func apiGetHistoryItem(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/history/")
	checkID, err := strconv.Atoi(path)
	if err != nil || checkID <= 0 {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid check ID"})
		return
	}

	if db == nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Database not connected"})
		return
	}

	query := `SELECT check_id, order_id, client_id, order_amount, risk_score, 
                     risk_level, risk_class, recommendation, top_factors, 
                     decision, checked_at, decided_at
              FROM check_history WHERE check_id = $1`

	row := db.QueryRow(query, checkID)

	var scanCheckID, scanOrderID, scanClientID int
	var orderAmt, riskScore sql.NullFloat64
	var riskLevel, riskClass, recommendation, decision sql.NullString
	var factorsStr sql.NullString
	var checkedAt, decidedAt sql.NullTime

	err = row.Scan(
		&scanCheckID,
		&scanOrderID,
		&scanClientID,
		&orderAmt,
		&riskScore,
		&riskLevel,
		&riskClass,
		&recommendation,
		&factorsStr,
		&decision,
		&checkedAt,
		&decidedAt,
	)

	if err != nil {
		if err == sql.ErrNoRows {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Check not found"})
		} else {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "Database error"})
		}
		return
	}

	record := DBRecord{
		"check_id":  scanCheckID,
		"order_id":  scanOrderID,
		"client_id": scanClientID,
	}

	if orderAmt.Valid {
		record["order_amount"] = orderAmt.Float64
	}
	if riskScore.Valid {
		record["risk_score"] = riskScore.Float64
	}
	if riskLevel.Valid {
		record["risk_level"] = riskLevel.String
	}
	if riskClass.Valid {
		record["risk_class"] = riskClass.String
	}
	if recommendation.Valid {
		record["recommendation"] = recommendation.String
	}
	if decision.Valid {
		record["decision"] = decision.String
	}
	if checkedAt.Valid {
		record["checked_at"] = checkedAt.Time.Format("2006-01-02 15:04:05")
	}
	if decidedAt.Valid {
		record["decided_at"] = decidedAt.Time.Format("2006-01-02 15:04:05")
	}

	if factorsStr.Valid && factorsStr.String != "" && factorsStr.String != "{}" {
		factors := strings.Trim(factorsStr.String, "{}")
		factorList := strings.Split(factors, ",")
		for i := range factorList {
			factorList[i] = strings.Trim(factorList[i], `"`)
		}
		record["top_factors"] = factorList
	}

	json.NewEncoder(w).Encode(record)
}

func apiHandleDecision(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)
	applyJSONHeaders(w)

	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Method not allowed"})
		return
	}

	var req struct {
		OrderID     int     `json:"order_id"`
		ClientID    int     `json:"client_id"`
		Decision    string  `json:"decision"`
		RiskScore   float64 `json:"risk_score"`
		OrderAmount float64 `json:"order_amount"`
		CheckID     int     `json:"check_id,omitempty"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]interface{}{"error": "Invalid request"})
		return
	}

	log.Printf("🎯 Decision received: order_id=%d, decision=%s, risk=%.2f",
		req.OrderID, req.Decision, req.RiskScore)

	if db != nil && req.OrderID > 0 && req.ClientID > 0 {
		var err error

		if req.CheckID > 0 {
			_, err = db.Exec(`
                UPDATE check_history 
                SET decision = $1, decided_at = NOW()
                WHERE check_id = $2 AND order_id = $3
            `, req.Decision, req.CheckID, req.OrderID)
		} else {
			_, err = db.Exec(`
                INSERT INTO check_history 
                (order_id, client_id, order_amount, risk_score, decision, checked_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
            `, req.OrderID, req.ClientID, req.OrderAmount, req.RiskScore, req.Decision)
		}

		if err != nil {
			log.Printf("⚠️ Ошибка сохранения решения: %v", err)
		} else {
			log.Printf("✅ Решение сохранено в БД")
		}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"success":  true,
		"check_id": req.CheckID,
		"redirect_params": map[string]string{
			"order_id":   strconv.Itoa(req.OrderID),
			"client_id":  strconv.Itoa(req.ClientID),
			"risk_score": fmt.Sprintf("%.2f", req.RiskScore),
			"decision":   req.Decision,
		},
	})
}

func resultPage(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w, r)

	orderID := r.URL.Query().Get("order_id")
	clientID := r.URL.Query().Get("client_id")
	riskScore := r.URL.Query().Get("risk_score")
	riskLevel := r.URL.Query().Get("risk_level")
	riskClass := r.URL.Query().Get("risk_class")
	recommendation := r.URL.Query().Get("recommendation")
	orderAmount := r.URL.Query().Get("order_amount")
	decision := r.URL.Query().Get("decision")
	checkID := r.URL.Query().Get("check_id")

	var topFactors []string
	i := 0
	for {
		factor := r.URL.Query().Get(fmt.Sprintf("factor_%d", i))
		if factor == "" {
			break
		}
		topFactors = append(topFactors, factor)
		i++
	}

	data := map[string]interface{}{
		"OrderID":        orderID,
		"ClientID":       clientID,
		"RiskScore":      riskScore,
		"RiskLevel":      riskLevel,
		"RiskClass":      riskClass,
		"Recommendation": recommendation,
		"OrderAmount":    orderAmount,
		"TopFactors":     topFactors,
		"Decision":       decision,
		"CheckID":        checkID,
		"DBConnected":    db != nil,
	}

	if checkID != "" && db != nil {
		if cid, err := strconv.Atoi(checkID); err == nil {
			query := `SELECT order_id, client_id, order_amount, risk_score, 
                             risk_level, risk_class, recommendation, top_factors
                      FROM check_history WHERE check_id = $1`
			row := db.QueryRow(query, cid)

			var record DBRecord = make(DBRecord)
			var orderAmt, riskScore sql.NullFloat64
			var riskLevel, riskClass, recommendation sql.NullString
			var factorsStr string

			var orderIDVal, clientIDVal int
			err := row.Scan(
				&orderIDVal, &clientIDVal, &orderAmt, &riskScore,
				&riskLevel, &riskClass, &recommendation, &factorsStr,
			)
			record["order_id"] = orderIDVal
			record["client_id"] = clientIDVal
			if err == nil {
				if orderAmt.Valid {
					record["order_amount"] = orderAmt.Float64
				}
				if riskScore.Valid {
					record["risk_score"] = riskScore.Float64
				}
				if riskLevel.Valid {
					record["risk_level"] = riskLevel.String
				}
				if riskClass.Valid {
					record["risk_class"] = riskClass.String
				}
				if recommendation.Valid {
					record["recommendation"] = recommendation.String
				}

				if factorsStr != "" && factorsStr != "{}" {
					factorsStr = strings.Trim(factorsStr, "{}")
					factors := strings.Split(factorsStr, ",")
					for i := range factors {
						factors[i] = strings.Trim(factors[i], `"`)
					}
					record["top_factors"] = factors
				}

				data["Record"] = record
			}
		}
	}

	tmpl, err := template.ParseFiles("templates/result.html")
	if err != nil {
		http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
		return
	}
	tmpl.Execute(w, data)
}

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
		log.Printf("⚠️ Не удалось загрузить модель: %v", err)
		pythonModelLoaded = false
	} else {
		pythonModelLoaded = true
		log.Println("✅ Модель успешно загружена")
	}

	if err := initDatabase(); err != nil {
		log.Printf("⚠️ Не удалось подключиться к БД: %v", err)
	}
	defer CloseDB()

	staticDir := filepath.Join(execDir, "static")
	if _, err := os.Stat(staticDir); os.IsNotExist(err) {
		log.Fatalf("Директория static не найдена: %s", staticDir)
	}

	http.HandleFunc("/static/", serveStatic)

	http.HandleFunc("/api/login", apiLogin)
	http.HandleFunc("/api/logout", apiLogout)

	http.HandleFunc("/api/stats", apiGetStats)
	http.HandleFunc("/api/chat", handleChat)

	http.HandleFunc("/api/client/orders", authMiddleware(apiGetClientOrders))
	http.HandleFunc("/api/client/returns", authMiddleware(apiClientReturnsHandler))
	http.HandleFunc("/api/client/", authMiddleware(apiGetClient))

	http.HandleFunc("/api/order/", apiGetOrder)
	http.HandleFunc("/api/orders", apiGetOrders)

	http.HandleFunc("/api/users", apiGetUsers)
	http.HandleFunc("/api/users/", apiGetUserDetail)
	http.HandleFunc("/api/search/users", apiSearchUsers)

	http.HandleFunc("/api/history", requireAdmin(apiGetHistory))
	http.HandleFunc("/api/history/", requireAdmin(apiGetHistoryItem))
	http.HandleFunc("/api/clear-history", requireAdmin(apiClearHistory))

	http.HandleFunc("/api/predict-fraud-v4", requireAdmin(apiPredictFraudV4))
	http.HandleFunc("/api/decision", requireAdmin(apiHandleDecision))
	http.HandleFunc("/check/decision", requireAdmin(apiHandleDecisionAlias))

	http.HandleFunc("/login", loginPage)
	http.HandleFunc("/result", requireAdmin(resultPage))
	http.HandleFunc("/check", requireAdmin(checkHandler))
	http.HandleFunc("/history", requireAdmin(historyPage))
	http.HandleFunc("/users", requireAdmin(usersPage))

	http.HandleFunc("/admin/profile", requireAdmin(adminProfileHandler))
	http.HandleFunc("/admin/chat", requireAdmin(adminChatHandler))
	http.HandleFunc("/client/profile", authMiddleware(clientProfileHandler))
	http.HandleFunc("/client/orders", authMiddleware(clientOrdersHandler))
	http.HandleFunc("/client/returns", authMiddleware(clientReturnsHandler))
	http.HandleFunc("/client/chat", authMiddleware(clientChatHandler))

	http.HandleFunc("/", rootHandler)

	port := getEnv("PORT", ":8083")
	fmt.Printf("FraudReturn Shield запущен на http://localhost%s\n", port)
	fmt.Printf("FastAPI сервис запущен на http://localhost:8000\n")

	if err := http.ListenAndServe(port, nil); err != nil {
		log.Fatal("Ошибка запуска сервера:", err)
	}
}

func serveStatic(w http.ResponseWriter, r *http.Request) {
	relativePath := strings.TrimPrefix(r.URL.Path, "/static/")
	path := filepath.Join(filepath.Join("static"), relativePath)

	if _, err := os.Stat(path); os.IsNotExist(err) {
		http.NotFound(w, r)
		return
	}

	ext := strings.ToLower(filepath.Ext(path))
	switch ext {
	case ".css":
		w.Header().Set("Content-Type", "text/css; charset=utf-8")
	case ".js":
		w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
	case ".png":
		w.Header().Set("Content-Type", "image/png")
	case ".jpg", ".jpeg":
		w.Header().Set("Content-Type", "image/jpeg")
	case ".svg":
		w.Header().Set("Content-Type", "image/svg+xml")
	case ".ico":
		w.Header().Set("Content-Type", "image/x-icon")
	case ".html":
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
	}

	http.ServeFile(w, r, path)
}

func rootHandler(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/api/") || strings.HasPrefix(r.URL.Path, "/static/") {
		http.NotFound(w, r)
		return
	}

	cookie, err := r.Cookie("user")
	if err != nil {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}
	decodedUser, err := base64.StdEncoding.DecodeString(cookie.Value)
	if err != nil {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}
	var userData struct {
		Role string `json:"role"`
	}
	if err := json.Unmarshal(decodedUser, &userData); err != nil {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}
	if userData.Role == "admin" {
		http.Redirect(w, r, "/admin/profile", http.StatusSeeOther)
	} else {
		http.Redirect(w, r, "/client/profile", http.StatusSeeOther)
	}
}

func startFastAPIService() {
	wd, err := os.Getwd()
	if err != nil {
		log.Printf("[ERROR] Не удалось получить рабочую директорию: %v", err)
		return
	}

	pythonPath := "python3"
	if _, err := exec.LookPath("python"); err == nil {
		pythonPath = "python"
	} else if _, err := exec.LookPath("py"); err == nil {
		pythonPath = "py"
	}

	scriptPath := filepath.Join(wd, "api.py")

	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		log.Printf("[WARN] FastAPI скрипт не найден: %s", scriptPath)
		return
	}

	cmd := exec.Command(pythonPath, scriptPath)
	cmd.Dir = wd
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = append(os.Environ(), "PYTHONUNBUFFERED=1")

	log.Printf("[INFO] Запуск FastAPI сервиса: %s %s", pythonPath, scriptPath)

	if err := cmd.Start(); err != nil {
		log.Printf("[ERROR] Не удалось запустить FastAPI: %v", err)
		return
	}

	go func() {
		if err := cmd.Wait(); err != nil {
			log.Printf("[ERROR] FastAPI сервис завершился с ошибкой: %v", err)
		} else {
			log.Println("[INFO] FastAPI сервис завершён")
		}
	}()
}

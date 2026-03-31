package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	_ "github.com/lib/pq"
)

// FormData — данные формы проверки
type FormData struct {
	OrderNumber       string  `json:"orderNumber"`
	OrderAmount       float64 `json:"orderAmount"`
	AccountAgeDays    int     `json:"accountAgeDays"`
	TotalOrders       int     `json:"totalOrders"`
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

// Глобальные переменные
var (
	pythonModelLoaded bool = false
	db                *sql.DB
)

// ============================================
// 🔌 Подключение к PostgreSQL
// ============================================
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

// ============================================
// 🗄️ Функции работы с базой данных
// ============================================
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

// ============================================
// 🚀 Основной сервер
// ============================================
func main() {
	pythonModelLoaded = true

	if err := initDatabase(); err != nil {
		log.Printf("⚠️ Не удалось подключиться к БД: %v (работа продолжится без БД)", err)
	}
	defer CloseDB()

	fs := http.FileServer(http.Dir("static"))
	http.Handle("/static/", http.StripPrefix("/static/", fs))

	http.HandleFunc("/", homePage)
	http.HandleFunc("/check", checkHandler)
	http.HandleFunc("/settings", settingsPage)
	http.HandleFunc("/history", historyPage)
	http.HandleFunc("/api/client/", apiGetClient)
	http.HandleFunc("/api/order/", apiGetOrder)
	http.HandleFunc("/api/stats", apiGetStats)

	port := getEnv("PORT", ":8081")
	fmt.Printf("🛡️ FraudReturn Shield запущен на http://localhost%s\n", port)

	err := http.ListenAndServe(port, nil)
	if err != nil {
		log.Fatal("❌ Ошибка запуска сервера:", err)
	}
}

// ============================================
// 📄 Обработчики страниц
// ============================================
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

		form := FormData{
			OrderNumber:       r.FormValue("orderNumber"),
			OrderAmount:       parseFloat(r.FormValue("orderAmount")),
			AccountAgeDays:    parseInt(r.FormValue("accountAgeDays")),
			TotalOrders:       parseInt(r.FormValue("totalOrders")),
			ReturnRate:        parseFloat(r.FormValue("returnRate")),
			DaysToReturn:      parseInt(r.FormValue("daysToReturn")),
			Category:          r.FormValue("category"),
			AddressMatch:      r.FormValue("addressMatch") == "on",
			DeviceNew:         r.FormValue("deviceNew") == "on",
			IsWeekend:         r.FormValue("isWeekend") == "on",
			ClientID:          parseInt(r.FormValue("clientID")),
			OrderID:           parseInt(r.FormValue("orderID")),
			HasTag:            r.FormValue("hasTag") == "on",
			HasReceipt:        r.FormValue("hasReceipt") == "on",
			HasDamage:         r.FormValue("hasDamage") == "on",
			IsUsed:            r.FormValue("isUsed") == "on",
			Reason:            r.FormValue("reason"),
			DaysSincePurchase: parseInt(r.FormValue("daysSincePurchase")),
			ReturnChannel:     r.FormValue("returnChannel"),
			TagsRemoved:       r.FormValue("tagsRemoved") == "on",
			MissingComponents: r.FormValue("missingComponents") == "on",
		}

		if form.OrderID > 0 && form.ClientID > 0 {
			if err := SaveReturnToDB(form); err != nil {
				log.Printf("⚠️ Не удалось сохранить возврат в БД: %v", err)
			}
		}

		result := calculateRisk(form)

		tmpl, err := template.ParseFiles("templates/result.html")
		if err != nil {
			http.Error(w, "Ошибка шаблона: "+err.Error(), 500)
			return
		}

		if err := tmpl.Execute(w, result); err != nil {
			http.Error(w, "Ошибка рендеринга: "+err.Error(), 500)
		}
		return
	}

	http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
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

// ============================================
// 🔗 API Handlers
// ============================================
func apiGetClient(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
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
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
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
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
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

// ============================================
// 🧮 Логика расчёта риска
// ============================================
func calculateRisk(f FormData) ResultData {
	features := prepareFeatures(f)

	log.Printf("[DEBUG] FormData: %+v", f)
	log.Printf("[DEBUG] Features count: %d", len(features))

	score, err := predictRisk(features)
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

	// Обогащение из БД
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
	features := make([]float32, 42)

	// 0. account_age_days
	features[0] = float32(f.AccountAgeDays) / 730.0

	// 1. total_purchases
	features[1] = float32(f.TotalOrders) / 100.0

	// 2. total_returns
	features[2] = float32(f.TotalOrders) * float32(f.ReturnRate) / 100.0

	// 3. customer_return_rate
	features[3] = float32(f.ReturnRate) / 100.0

	// 4. order_amount
	features[4] = float32(f.OrderAmount) / 200000.0

	// 5. category
	categoryMap := map[string]float32{
		"electronics": 0,
		"clothing":    1,
		"cosmetics":   2,
		"books":       3,
		"sports":      4,
		"home":        5,
	}
	features[5] = categoryMap[f.Category]

	// 6. high_value_flag
	if f.OrderAmount > 30000 {
		features[6] = 1.0
	} else {
		features[6] = 0.0
	}

	// 7. weekend_purchase
	if f.IsWeekend {
		features[7] = 1.0
	} else {
		features[7] = 0.0
	}

	// 8. address_match
	if f.AddressMatch {
		features[8] = 1.0
	} else {
		features[8] = 0.0
	}

	// 9. device_new
	if f.DeviceNew {
		features[9] = 1.0
	} else {
		features[9] = 0.0
	}

	// 10. receipt_provided
	if f.HasReceipt {
		features[10] = 1.0
	} else {
		features[10] = 0.0
	}

	// 11. claimed_reason
	reasonMap := map[string]float32{
		"defect":       2,
		"size":         0,
		"color":        1,
		"quality":      2,
		"changed_mind": 1,
		"other":        14,
	}
	features[11] = reasonMap[f.Reason]

	// 12. discount_percent
	features[12] = 5.0

	// 13. promo_code_used
	features[13] = 0.0

	// 14. first_order_discount_abuse
	features[14] = 0.0

	// 15. is_electronics
	if f.Category == "electronics" {
		features[15] = 1.0
	} else {
		features[15] = 0.0
	}

	// 16. items_in_order
	features[16] = 2.0

	// 17. payment_method_risk
	features[17] = 0.2

	// 18. chargeback_history_90d
	features[18] = 0.0

	// 19. card_bin_country_mismatch
	features[19] = 0.0

	// 20. shipping_region_risk
	features[20] = 0.2

	// 21. delivery_address_type
	features[21] = 0.0

	// 22. distance_from_registration_city
	features[22] = 50.0

	// 23. order_hour
	features[23] = 12.0

	// 24. order_time_night
	features[24] = 0.0

	// 25. ip_velocity_24h
	features[25] = 2.0

	// 26. ip_velocity_7d
	features[26] = 5.0

	// 27. accounts_per_ip
	features[27] = 1.0

	// 28. accounts_per_phone
	features[28] = 1.0

	// 29. accounts_per_device
	features[29] = 1.0

	// 30. device_is_emulator
	features[30] = 0.0

	// 31. device_trust_score
	features[31] = 0.8

	// 32. ip_trust_score
	features[32] = 0.8

	// 33. avg_order_amount
	features[33] = float32(f.OrderAmount) / 200000.0

	// 34. return_rate_30d
	features[34] = float32(f.ReturnRate) / 100.0

	// 35. refund_velocity_7d
	features[35] = 1.0

	// 36. refund_velocity_30d
	features[36] = 3.0

	// 37. support_ticket_count_30d
	features[37] = 1.0

	// 38. review_count_30d
	features[38] = 2.0

	// 39. negative_review_cluster
	features[39] = 0.0

	// 40. threat_language_detected
	features[40] = 0.0

	// 41. legal_claim_threat
	features[41] = 0.0

	return features
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

	if score > 1.0 {
		score = 1.0
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
// 🐍 Python ONNX интеграция
// ============================================
func loadModel(modelPath string) error {
	cmd := exec.Command("python", "model.py", "--load", modelPath)
	output, err := cmd.CombinedOutput()

	jsonStart := bytes.IndexByte(output, '{')
	jsonEnd := bytes.LastIndexByte(output, '}')
	if jsonStart == -1 || jsonEnd == -1 || jsonEnd <= jsonStart {
		return fmt.Errorf("нет JSON в выводе: %s", string(output))
	}

	jsonBytes := output[jsonStart : jsonEnd+1]

	if err != nil {
		return fmt.Errorf("%s: %v", string(jsonBytes), err)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(jsonBytes, &result); err != nil {
		return fmt.Errorf("JSON ошибка: %v | output: %s", err, string(jsonBytes))
	}

	if result["success"] == false {
		return fmt.Errorf("%v", result["error"])
	}

	log.Println("[INFO] Модель ONNX загружена")
	return nil
}

func predictRisk(features []float32) (float32, error) {
	var featuresStr strings.Builder
	for i, f := range features {
		if i > 0 {
			featuresStr.WriteString(",")
		}
		featuresStr.WriteString(fmt.Sprintf("%.6f", f))
	}

	wd, _ := os.Getwd()
	modelPath := filepath.Join(wd, "fraud_model_v3_27patterns.onnx")

	cmd := exec.Command("python", "model.py", "--predict", modelPath, featuresStr.String())
	cmd.Dir = wd
	output, err := cmd.CombinedOutput()

	jsonStart := bytes.IndexByte(output, '{')
	jsonEnd := bytes.LastIndexByte(output, '}')
	if jsonStart == -1 || jsonEnd == -1 || jsonEnd <= jsonStart {
		return 0, fmt.Errorf("нет JSON в выводе: %s", string(output))
	}

	jsonBytes := output[jsonStart : jsonEnd+1]

	if err != nil {
		log.Printf("[ERROR] Python: %s", string(jsonBytes))
		return 0, fmt.Errorf("%s: %v", string(jsonBytes), err)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(jsonBytes, &result); err != nil {
		log.Printf("[ERROR] JSON: %v | output: %s", err, string(jsonBytes))
		return 0, err
	}

	if result["success"] == false {
		log.Printf("[ERROR] Модель: %v", result["error"])
		return 0, fmt.Errorf("%v", result["error"])
	}

	score, ok := result["score"].(float64)
	if !ok {
		return 0, fmt.Errorf("неверный формат score")
	}

	log.Printf("[INFO] ONNX prediction: %.4f", score)
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

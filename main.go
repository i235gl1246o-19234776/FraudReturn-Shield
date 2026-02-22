package main

import (
	"fmt"
	"html/template"
	"log"
	"net/http"
)

type FormData struct {
	OrderNumber string
	Category    string
	HasTag      bool
	HasReceipt  bool
	HasDamage   bool
	IsUsed      bool
	Reason      string
}

type ResultData struct {
	FormData
	RiskScore        float64
	RiskLevel        string
	RiskClass        string
	Recommendation   string
	TopFactors       []string
	StrokeDashOffset float64 // ← ДОБАВЬТЕ ЭТО ПОЛЕ
	RiskPercent      int     // ← Для отображения процентов
}

func main() {
	// Статика
	fs := http.FileServer(http.Dir("static"))
	http.Handle("/static/", http.StripPrefix("/static/", fs))

	// Маршруты
	http.HandleFunc("/", homePage)
	http.HandleFunc("/check", checkHandler)

	port := ":8081"
	fmt.Printf("🛡️  FraudReturn Shield запущен на http://localhost%s\n", port)
	err := http.ListenAndServe(port, nil)
	if err != nil {
		log.Fatal("Ошибка запуска:", err)
	}
}

func homePage(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	tmpl, err := template.ParseFiles("templates/index.html")
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	tmpl.Execute(w, nil)
}

func checkHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Redirect(w, r, "/", 302)
		return
	}
	r.ParseForm()

	form := FormData{
		OrderNumber: r.FormValue("orderNumber"),
		Category:    r.FormValue("category"),
		HasTag:      r.FormValue("hasTag") == "on",
		HasReceipt:  r.FormValue("hasReceipt") == "on",
		HasDamage:   r.FormValue("hasDamage") == "on",
		IsUsed:      r.FormValue("isUsed") == "on",
		Reason:      r.FormValue("reason"),
	}

	result := calculateRisk(form)

	tmpl, err := template.ParseFiles("templates/result.html")
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	tmpl.Execute(w, result)
}

func calculateRisk(f FormData) ResultData {
	score := 0.0
	factors := []string{}

	if !f.HasTag {
		score += 0.25
		factors = append(factors, "Бирка отсутствует")
	}
	if !f.HasReceipt {
		score += 0.15
		factors = append(factors, "Чек не предоставлен")
	}
	if f.HasDamage {
		score += 0.20
		factors = append(factors, "Есть повреждения товара")
	}
	if f.IsUsed {
		score += 0.25
		factors = append(factors, "Товар имеет следы использования")
	}
	if f.Reason == "changed_mind" {
		score += 0.15
		factors = append(factors, "Возврат без объективной причины")
	}

	if score > 1.0 {
		score = 1.0
	}

	level := "Низкий"
	class := "low"
	recommendation := "✅ Одобрить" // ← По умолчанию одобряем

	if score > 0.30 && score <= 0.65 {
		level = "Средний"
		class = "medium"
		recommendation = "⚠️ На проверку"
	} else if score > 0.65 {
		level = "Высокий"
		class = "high"
		recommendation = "❌ Отклонить"
	}

	return ResultData{
		FormData:         f,
		RiskScore:        score,
		RiskLevel:        level,
		RiskClass:        class,
		Recommendation:   recommendation,
		TopFactors:       factors,
		StrokeDashOffset: (1 - score) * 283, // ← СЧИТАЕМ В GO
		RiskPercent:      int(score * 100),  // ← Для отображения
	}
}

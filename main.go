package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
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
	StrokeDashOffset float64
	RiskPercent      int
}

// Глобальный флаг загрузки модели
var pythonModelLoaded bool = false

func main() {
	// 🔧 ЗАГРУЖАЕМ МОДЕЛЬ ПРИ СТАРТЕ
	modelPath := "dummy_fraud_model.onnx"
	if _, err := os.Stat(modelPath); os.IsNotExist(err) {
		log.Printf("⚠️ Модель не найдена: %s, используем заглушку", modelPath)
		pythonModelLoaded = false
	} else {
		err := loadModel(modelPath)
		if err != nil {
			log.Printf("⚠️ Ошибка загрузки модели: %v, используем заглушку", err)
			pythonModelLoaded = false
		} else {
			pythonModelLoaded = true
			log.Println("✅ Модель ONNX успешно загружена!")
		}
	}

	fs := http.FileServer(http.Dir("static"))
	http.Handle("/static/", http.StripPrefix("/static/", fs))
	http.HandleFunc("/", homePage)
	http.HandleFunc("/check", checkHandler)

	port := ":8081"
	fmt.Printf("🛡️ FraudReturn Shield запущен на http://localhost%s\n", port)
	err := http.ListenAndServe(port, nil)
	if err != nil {
		log.Fatal("Ошибка запуска:", err)
	}
}

func loadModel(modelPath string) error {
	cmd := exec.Command("python", "model.py", "--load", modelPath)
	output, err := cmd.CombinedOutput()

	// 🔧 Находим начало JSON ({) и конец (})
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

	fmt.Println("[INFO] Модель ONNX загружена")
	return nil
}

func predictRisk(features []float32) (float32, error) {
	featuresStr := ""
	for i, f := range features {
		if i > 0 {
			featuresStr += ","
		}
		featuresStr += fmt.Sprintf("%.6f", f)
	}

	wd, _ := os.Getwd()
	modelPath := filepath.Join(wd, "dummy_fraud_model.onnx")

	cmd := exec.Command("python", "model.py", "--predict", modelPath, featuresStr)
	cmd.Dir = wd
	output, err := cmd.CombinedOutput()

	// 🔧 Находим только JSON часть
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

func homePage(w http.ResponseWriter, r *http.Request) {
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
	features := prepareFeatures(f)

	score, err := predictRisk(features)
	if err != nil {
		log.Printf("⚠️ Python ONNX ошибка: %v, используем заглушку", err)
		score = calculateRiskFallback(f)
	}

	if score < 0 {
		score = 0
	}
	if score > 1 {
		score = 1
	}

	level := "Низкий"
	class := "low"
	recommendation := "✅ Одобрить"

	if score > 0.30 && score <= 0.65 {
		level = "Средний"
		class = "medium"
		recommendation = "⚠️ На проверку"
	} else if score > 0.65 {
		level = "Высокий"
		class = "high"
		recommendation = "❌ Отклонить"
	}

	factors := getRiskFactors(f, score)

	return ResultData{
		FormData:         f,
		RiskScore:        float64(score),
		RiskLevel:        level,
		RiskClass:        class,
		Recommendation:   recommendation,
		TopFactors:       factors,
		StrokeDashOffset: (1 - float64(score)) * 283,
		RiskPercent:      int(float64(score) * 100),
	}
}

func prepareFeatures(f FormData) []float32 {
	features := make([]float32, 24)

	features[0] = 1.0
	if !f.HasTag {
		features[0] = 0.0
	}

	features[1] = 1.0
	if !f.HasReceipt {
		features[1] = 0.0
	}

	features[2] = 0.0
	if f.HasDamage {
		features[2] = 1.0
	}

	features[3] = 0.0
	if f.IsUsed {
		features[3] = 1.0
	}

	switch f.Reason {
	case "defect":
		features[4] = 1.0
	case "size":
		features[5] = 1.0
	case "color":
		features[6] = 1.0
	case "quality":
		features[7] = 1.0
	case "changed_mind":
		features[8] = 1.0
	}

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
	if score > 1.0 {
		score = 1.0
	}
	return float32(score)
}

func getRiskFactors(f FormData, score float32) []string {
	factors := []string{}
	if !f.HasTag {
		factors = append(factors, "Бирка отсутствует")
	}
	if !f.HasReceipt {
		factors = append(factors, "Чек не предоставлен")
	}
	if f.HasDamage {
		factors = append(factors, "Есть повреждения товара")
	}
	if f.IsUsed {
		factors = append(factors, "Товар имеет следы использования")
	}
	if f.Reason == "changed_mind" {
		factors = append(factors, "Возврат без объективной причины")
	}
	return factors
}

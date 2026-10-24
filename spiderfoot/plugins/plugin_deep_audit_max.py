import spiderfoot.pluginbase as sfp
import json
import re
import urllib.request
import urllib.error
import urllib.parse
from bs4 import BeautifulSoup
import time
import os

# Importações para Selenium (Navegador Real)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support.expected_conditions import presence_of_element_located
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

class SpiderFootPluginDeepAuditMax(sfp.SpiderFootPlugin):
    """
    MÓDULO DE AUDITORIA MÁXIMA - VERSÃO ATIVA COM SELENIUM

    Diferente da versão anterior, esta usa um navegador real (Chrome Headless)
    para acessar sites que bloqueiam bots simples.

    Fontes:
    - Imagens (Google, Bing, Yandex)
    - Social Media (Twitter/X, Reddit, TikTok)
    - Judicial (JusBrasil scraping ativo)
    - Breaches (HaveIBeenPwned API)
    - Notícias (Google News)
    """

    meta = {
        'name': 'Deep Audit Max Active',
        'summary': 'Busca ativa com Selenium para contornar bloqueios de bots.',
        'flags': ['active'],
        'score': 1.0,
        'description': 'Varredura agressiva usando navegador headless para máxima coleta de dados.'
    }

    configdefaults = {
        'max_pages': '50',
        'include_judicial': 'True',
        'include_images': 'True',
        'include_social': 'True',
        'include_breaches': 'True',
        'headless': 'True' # Use 'False' para ver o navegador abrindo (debug)
    }

    def setup(self, config):
        self._config = config or {}
        self.foundResults = []
        self.driver = None

        # Configuração do Navegador Selenium
        if HAS_SELENIUM:
            chrome_options = Options()
            if self._config.get('headless', 'True') == 'True':
                chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            try:
                # Inicializa o driver (o webdriver-manager baixa o chrome automaticamente se faltar)
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
            except Exception as e:
                print(f"Aviso: Não foi possível iniciar o Selenium: {e}")
                self.driver = None

    def producedTypes(self):
        return [
            "EMAIL_ADDRESS", "PHONE_NUMBER", "IP_ADDRESS", "DOMAIN_NAME",
            "SOCIAL_MEDIA_HANDLE", "WEBSITE_DYNAMIC_CONTENT",
            "IMAGE_FILE", "JUDICIAL_RECORD", "MEDICAL_RECORD",
            "DATING_PROFILE", "BOLETIM_OCORRENCIA", "DEBT_REGISTER",
            "DATA_BREACH", "GOVERNMENT_RECORD", "NEWS_ARTICLE",
            "TWITTER_HANDLE", "TIKTOK_USERNAME"
        ]

    def query(self, event):
        evt_type = event.eventType
        data = event.data

        results = []

        # --- 1. BUSCA ATIVA DE IMAGENS (SELENIUM) ---
        if self._config.get('include_images', 'True') == 'True' and evt_type in ["NAME", "EMAIL_ADDRESS"] and self.driver:
            try:
                # Google Imagens
                google_img_url = f"https://www.google.com/search?tbm=isch&q={urllib.parse.quote(data)}"
                self.driver.get(google_img_url)

                # Espera carregar os resultados
                wait = WebDriverWait(self.driver, 10)
                images = wait.until(presence_of_element_located((By.CSS_SELECTOR, "img")))

                # Tenta extrair URLs de imagens (classe comum do Google)
                img_urls = self.driver.find_elements(By.CSS_SELECTOR, "img[src]")
                count = 0
                for img in img_urls[:10]: # Pega top 10
                    src = img.get_attribute("src")
                    if src and len(src) > 20:
                        results.append({
                            "type": "IMAGE_FILE",
                            "data": f"Imagem Google: {src[:100]}...",
                            "confidence": 0.9,
                            "source_url": google_img_url
                        })
                        count += 1

                if count == 0:
                    # Fallback se não achar img tag direta
                    results.append({"type": "IMAGE_FILE", "data": f"Busca realizada no Google Imagens: {google_img_url}", "confidence": 0.5})

            except Exception as e:
                pass # Silencioso para não poluir log

        # --- 2. BUSCA JUDICIAL ATIVA (JUSBRAZIL SCRAPING) ---
        if self._config.get('include_judicial', 'True') == 'True' and evt_type in ["NAME", "CPF"] and self.driver:
            try:
                js_url = f"https://www.jusbrasil.com.br/busca?q={urllib.parse.quote(data)}"
                self.driver.get(js_url)

                wait = WebDriverWait(self.driver, 10)
                # Tenta encontrar elementos de resultado comuns do JusBrasil
                items = self.driver.find_elements(By.CSS_SELECTOR, ".result-item, .search-result")

                for item in items[:5]:
                    title_elem = item.find_element(By.TAG_NAME, "h2") if item.find_elements(By.TAG_NAME, "h2") else None
                    if title_elem:
                        title = title_elem.text.strip()
                        if title:
                            results.append({
                                "type": "JUDICIAL_RECORD",
                                "data": f"Processo/Título: {title}",
                                "confidence": 0.85,
                                "source_url": js_url
                            })

            except Exception:
                pass

        # --- 3. VAZAMENTOS DE DADOS (API DIRETA - MAIS CONFIÁVEL QUE SCRAPE) ---
        if self._config.get('include_breaches', 'True') == 'True' and evt_type == "EMAIL_ADDRESS":
            hibp_url = f"https://haveibeenpwned.com/api/v3/pasteaccount/{data}"
            try:
                req = urllib.request.Request(hibp_url, headers={'User-Agent': 'SpiderFoot-Max'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        pastes = json.loads(response.read().decode('utf-8'))
                        if pastes:
                            for paste in pastes[:5]:
                                results.append({
                                    "type": "DATA_BREACH",
                                    "data": f"Vazamento: {paste.get('Title', 'Desconhecido')} ({paste.get('SiteDomain', 'N/A')})",
                                    "confidence": 0.95,
                                    "source_url": f"https://haveibeenpwned.com/Pastes/{data}"
                                })
            except urllib.error.HTTPError:
                pass

        # --- 4. REDES SOCIAIS ATIVAS (TWITTER/REDDIT) ---
        if self._config.get('include_social', 'True') == 'True' and self.driver:
            # Twitter/X Search
            try:
                tw_url = f"https://twitter.com/search?q={urllib.parse.quote(data)}&src=typed_query"
                self.driver.get(tw_url)
                time.sleep(3) # Espera renderizar
                tweets = self.driver.find_elements(By.CSS_SELECTOR, "div.css-1dbjc4n")
                for t in tweets[:3]:
                    text = t.text.strip()
                    if text:
                        results.append({
                            "type": "TWITTER_HANDLE",
                            "data": f"Tuit encontrado: {text[:100]}",
                            "confidence": 0.7,
                            "source_url": tw_url
                        })
            except:
                pass

            # Reddit Search
            try:
                reddit_url = f"https://www.reddit.com/search/?q={urllib.parse.quote(data)}&sort=relevance&t=all"
                self.driver.get(reddit_url)
                time.sleep(3)
                posts = self.driver.find_elements(By.CSS_SELECTOR, "div.title")
                for p in posts[:3]:
                    title = p.text.strip()
                    if title:
                        results.append({
                            "type": "SOCIAL_MEDIA_HANDLE",
                            "data": f"Post Reddit: {title}",
                            "confidence": 0.7,
                            "source_url": reddit_url
                        })
            except:
                pass

        # --- 5. NOTÍCIAS (GOOGLE NEWS) ---
        if evt_type in ["NAME", "CNPJ"]:
            news_url = f"https://news.google.com/search?q={urllib.parse.quote(data)}&hl=pt-BR&gl=BR&ceid=BR:pt-001"
            try:
                req = urllib.request.Request(news_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    html = response.read().decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(html, 'html.parser')
                    articles = soup.find_all('a', href=True)
                    for a in articles[:5]:
                        title = a.get_text(strip=True)
                        if title and len(title) > 10:
                            results.append({
                                "type": "NEWS_ARTICLE",
                                "data": f"Notícia: {title}",
                                "confidence": 0.8,
                                "source_url": f"https://news.google.com{a['href']}"
                            })
            except:
                pass

        return results

    def cleanup(self):
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
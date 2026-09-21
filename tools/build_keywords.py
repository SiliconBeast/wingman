"""Regenerate docs/keywords.json (shared by the scanner and the dashboard). Edit TERMS below."""
import json, re
from pathlib import Path

# (label, regex or None to match the label literally, case_sensitive, group)
TERMS = [
 # languages
 ("C", "C", 1, "lang"), ("C++", r"C\+\+|cpp", 0, "lang"), ("C#", "C#", 0, "lang"), ("Python", None, 0, "lang"),
 ("Java", r"Java(?!\s*Script)", 0, "lang"), ("JavaScript", None, 0, "lang"), ("TypeScript", None, 0, "lang"),
 ("Go", r"Golang|Go(?=\s*(?:,|/|\)|and\b|or\b|programming|language|services))", 1, "lang"), ("Rust", "Rust", 1, "lang"),
 ("Kotlin", None, 0, "lang"), ("Swift", r"Swift(?:UI)?", 1, "lang"), ("Scala", None, 0, "lang"), ("MATLAB", None, 0, "lang"),
 ("Simulink", None, 0, "lang"), ("Bash", r"Bash|shell script(?:ing|s)?", 0, "lang"), ("SQL", "SQL", 0, "lang"),
 ("Assembly", r"assembly language|assembly programming|ARM assembly|x86 assembly", 0, "lang"), ("CUDA", None, 0, "lang"),
 ("OpenCL", None, 0, "lang"), ("Verilog", None, 0, "lang"), ("SystemVerilog", r"SystemVerilog|System Verilog", 0, "lang"),
 ("VHDL", None, 0, "lang"), ("Chisel", "Chisel", 1, "lang"), ("SystemC", None, 0, "lang"), ("HLS", r"HLS|high-level synthesis", 0, "lang"),
 ("Tcl", r"Tcl|TCL", 1, "lang"), ("Perl", None, 0, "lang"), ("Ruby", r"Ruby(?: on Rails)?|Rails", 1, "lang"), ("PHP", None, 0, "lang"),
 ("Haskell", None, 0, "lang"), ("OCaml", None, 0, "lang"), ("LabVIEW", None, 0, "lang"),
 # digital design & verification
 ("RTL", None, 0, "hw"), ("ASIC", r"ASICs?", 0, "hw"), ("FPGA", r"FPGAs?", 0, "hw"), ("SoC", r"SoCs?|system[- ]on[- ]chip", 0, "hw"),
 ("UVM", None, 0, "hw"), ("formal verification", None, 0, "hw"), ("assertions", r"SVA|SystemVerilog assertions|assertion-based", 0, "hw"),
 ("functional coverage", r"functional coverage|code coverage|coverage closure", 0, "hw"), ("testbench", r"test ?benches|testbench(?:es)?", 0, "hw"),
 ("DFT", r"DFT|design for test(?:ability)?", 0, "hw"), ("ATPG", None, 0, "hw"), ("BIST", None, 0, "hw"),
 ("STA", r"STA|static timing analysis", 0, "hw"), ("timing closure", None, 0, "hw"), ("synthesis", r"logic synthesis|RTL synthesis|synthesis", 0, "hw"),
 ("place and route", r"place (?:and|&) route|P&R|PnR", 0, "hw"), ("physical design", None, 0, "hw"), ("floorplanning", r"floor ?planning", 0, "hw"),
 ("CDC", r"CDC|clock domain crossing", 0, "hw"), ("lint", r"linting|Spyglass", 0, "hw"), ("gate-level simulation", r"gate[- ]level sim(?:ulation)?s?", 0, "hw"),
 ("emulation", r"emulation|Palladium|ZeBu", 0, "hw"), ("microarchitecture", r"micro-?architecture", 0, "hw"), ("computer architecture", None, 0, "hw"),
 ("pipelining", r"pipelin(?:e|ed|ing)", 0, "hw"), ("cache coherence", r"cache coheren(?:ce|cy)", 0, "hw"), ("caches", r"caches|cache hierarch(?:y|ies)", 0, "hw"),
 ("branch prediction", None, 0, "hw"), ("out-of-order", r"out[- ]of[- ]order", 0, "hw"), ("RISC-V", r"RISC-?V", 0, "hw"),
 ("Arm", r"ARM|Arm(?= (?:Cortex|architecture|processors?|cores?|assembly))|Cortex-[AMR]\d*", 1, "hw"), ("x86", r"x86(?:-64)?", 0, "hw"),
 ("DSP", r"DSPs?|digital signal processing", 0, "hw"), ("GPU", r"GPUs?", 0, "hw"), ("NPU", r"NPUs?", 0, "hw"),
 ("AI accelerators", r"AI accelerators?|ML accelerators?|neural (?:network )?accelerators?", 0, "hw"),
 ("tapeout", r"tape-?outs?", 0, "hw"), ("post-silicon validation", r"post-?silicon(?: validation)?|silicon validation", 0, "hw"),
 ("bring-up", r"bring-?up", 0, "hw"),
 # interfaces & RF
 ("SerDes", None, 0, "io"), ("PCIe", r"PCIe|PCI Express", 0, "io"), ("DDR", r"LPDDR\d?|DDR\d?", 0, "io"), ("HBM", None, 0, "io"),
 ("Ethernet", None, 0, "io"), ("USB", None, 0, "io"), ("AXI", r"AXI\d?|AMBA|AHB|APB", 0, "io"), ("I2C", r"I2C|I²C|IIC", 0, "io"),
 ("SPI", "SPI", 1, "io"), ("UART", r"UARTs?", 0, "io"), ("CAN bus", r"CAN(?:[ -]?(?:bus|FD))?", 1, "io"), ("JTAG", None, 0, "io"),
 ("MIPI", None, 0, "io"), ("Bluetooth", r"Bluetooth|BLE", 0, "io"), ("Wi-Fi", r"Wi-?Fi|802\.11\w*", 0, "io"), ("5G", r"5G(?: NR)?", 0, "io"),
 ("LTE", None, 0, "io"), ("RF", "RF", 1, "io"), ("antenna design", r"antennas?", 0, "io"), ("mmWave", r"mm-?[Ww]ave", 0, "io"),
 ("VNA", r"VNA|network analy[sz]ers?", 0, "io"), ("spectrum analyzer", r"spectrum analy[sz]ers?", 0, "io"),
 ("oscilloscope", r"oscilloscopes?", 0, "io"), ("logic analyzer", r"logic analy[sz]ers?", 0, "io"),
 ("signal integrity", None, 0, "io"), ("power integrity", None, 0, "io"), ("EMC/EMI", r"EMC|EMI", 0, "io"),
 # boards, analog, power
 ("PCB design", r"PCBs?|printed circuit boards?", 0, "board"), ("schematic capture", r"schematics?(?: capture)?", 0, "board"),
 ("Altium", None, 0, "board"), ("KiCad", None, 0, "board"), ("OrCAD", None, 0, "board"), ("Allegro", None, 0, "board"),
 ("SPICE", r"SPICE|LTspice|HSPICE|PSpice|ngspice", 0, "board"), ("Spectre", None, 0, "board"), ("Virtuoso", None, 0, "board"),
 ("analog design", r"analog(?: circuit)? design|analog circuits?", 0, "board"), ("mixed-signal", r"mixed[- ]signal", 0, "board"),
 ("ADC", r"ADCs?", 0, "board"), ("DAC", r"DACs?", 0, "board"), ("PLL", r"PLLs?", 0, "board"), ("LDO", r"LDOs?", 0, "board"),
 ("op-amps", r"op-?amps?|operational amplifiers?", 0, "board"), ("power electronics", None, 0, "board"),
 ("DC-DC converters", r"DC-?DC(?: converters?)?|buck converters?|boost converters?", 0, "board"),
 ("battery management", r"BMS|battery management", 0, "board"), ("motor control", None, 0, "board"),
 ("soldering", r"solder(?:ing)?", 0, "board"), ("lab equipment", r"lab equipment|bench testing|test equipment", 0, "board"),
 # embedded
 ("embedded systems", r"embedded (?:systems?|software|C|firmware|development|programming)", 0, "emb"), ("firmware", None, 0, "emb"),
 ("bare-metal", r"bare[- ]metal", 0, "emb"), ("RTOS", r"RTOS|real-time operating systems?", 0, "emb"), ("FreeRTOS", None, 0, "emb"),
 ("Zephyr", None, 0, "emb"), ("embedded Linux", None, 0, "emb"), ("Yocto", None, 0, "emb"),
 ("device drivers", r"(?:device|kernel|Linux) drivers?|driver development", 0, "emb"),
 ("Linux kernel", r"Linux kernel|kernel development", 0, "emb"), ("bootloader", r"boot ?loaders?|U-Boot", 0, "emb"),
 ("microcontrollers", r"microcontrollers?|MCUs?", 0, "emb"), ("STM32", None, 0, "emb"), ("ESP32", None, 0, "emb"),
 ("Arduino", None, 0, "emb"), ("Raspberry Pi", None, 0, "emb"),
 # EDA tools
 ("Vivado", None, 0, "eda"), ("Vitis", None, 0, "eda"), ("Quartus", None, 0, "eda"), ("ModelSim/Questa", r"ModelSim|Questa\w*", 0, "eda"),
 ("VCS", "VCS", 1, "eda"), ("Xcelium", None, 0, "eda"), ("Verdi", None, 0, "eda"), ("Design Compiler", None, 0, "eda"),
 ("Innovus", None, 0, "eda"), ("PrimeTime", None, 0, "eda"), ("Calibre", None, 0, "eda"), ("JasperGold", r"JasperGold|Jasper", 0, "eda"),
 ("cocotb", None, 0, "eda"), ("Verilator", None, 0, "eda"), ("Yosys", None, 0, "eda"), ("OpenLane", r"OpenLane|OpenROAD", 0, "eda"),
 # software & systems
 ("Linux", None, 0, "sw"), ("Git", r"Git(?!Hub|Lab)", 0, "sw"), ("CI/CD", r"CI/CD|continuous integration", 0, "sw"),
 ("GitHub Actions", None, 0, "sw"), ("Jenkins", None, 0, "sw"), ("Docker", None, 0, "sw"), ("Kubernetes", r"Kubernetes|k8s", 0, "sw"),
 ("Terraform", None, 0, "sw"), ("AWS", r"AWS|Amazon Web Services", 0, "sw"), ("GCP", r"GCP|Google Cloud", 0, "sw"), ("Azure", None, 0, "sw"),
 ("REST APIs", r"REST(?:ful)?(?: APIs?)?", 0, "sw"), ("GraphQL", None, 0, "sw"), ("gRPC", None, 0, "sw"), ("gNMI", None, 0, "sw"),
 ("YANG", "YANG", 1, "sw"), ("NETCONF", None, 0, "sw"), ("OpenConfig", None, 0, "sw"), ("telemetry", None, 0, "sw"),
 ("fuzzing", r"fuzz(?:ing|er|ers)?|fuzz testing", 0, "sw"), ("Wireshark", None, 0, "sw"), ("Prometheus", None, 0, "sw"), ("Grafana", None, 0, "sw"),
 ("microservices", None, 0, "sw"), ("distributed systems", None, 0, "sw"), ("concurrency", r"concurren(?:cy|t programming)", 0, "sw"),
 ("multithreading", r"multi-?thread(?:ed|ing)", 0, "sw"),
 ("networking", r"computer networking|network(?:ing)? protocols?|network programming|networking stack", 0, "sw"),
 ("TCP/IP", r"TCP/IP|TCP|UDP", 0, "sw"), ("data structures", None, 0, "sw"), ("algorithms", None, 0, "sw"),
 ("object-oriented design", r"object[- ]oriented|OOP", 0, "sw"), ("design patterns", None, 0, "sw"),
 ("unit testing", r"unit tests?|unit testing", 0, "sw"), ("test automation", r"test automation|automated test(?:s|ing)?", 0, "sw"),
 ("pytest", None, 0, "sw"), ("GoogleTest", r"GoogleTest|gtest", 0, "sw"), ("JUnit", None, 0, "sw"), ("GDB", None, 0, "sw"),
 ("Valgrind", None, 0, "sw"), ("profiling", r"profil(?:ing|ers?)", 0, "sw"),
 ("performance optimization", r"performance (?:optimi[sz]ation|tuning)", 0, "sw"), ("low latency", r"low[- ]latency", 0, "sw"),
 ("React", r"React(?:\.js)?", 0, "sw"), ("Angular", None, 0, "sw"), ("Vue", r"Vue(?:\.js)?", 0, "sw"), ("Node.js", r"Node(?:\.js)?", 0, "sw"),
 ("Next.js", None, 0, "sw"), ("Django", None, 0, "sw"), ("Flask", None, 0, "sw"), ("FastAPI", None, 0, "sw"),
 ("Spring", r"Spring(?: Boot)?", 1, "sw"), (".NET", r"\.NET", 0, "sw"), ("Android", None, 0, "sw"), ("iOS", "iOS", 1, "sw"),
 ("PostgreSQL", r"PostgreSQL|Postgres", 0, "sw"), ("MySQL", None, 0, "sw"), ("MongoDB", None, 0, "sw"), ("Redis", None, 0, "sw"),
 ("Kafka", None, 0, "sw"), ("Spark", r"Apache Spark|PySpark|Spark", 1, "sw"), ("Airflow", None, 0, "sw"), ("Snowflake", None, 0, "sw"),
 ("Elasticsearch", None, 0, "sw"), ("Agile", r"Agile|Scrum", 1, "sw"), ("Jira", r"Jira|JIRA", 1, "sw"),
 # AI & data
 ("machine learning", None, 0, "ai"), ("deep learning", None, 0, "ai"), ("PyTorch", None, 0, "ai"), ("TensorFlow", None, 0, "ai"),
 ("JAX", "JAX", 1, "ai"), ("scikit-learn", r"scikit-learn|sklearn", 0, "ai"), ("pandas", None, 0, "ai"), ("NumPy", None, 0, "ai"),
 ("SciPy", None, 0, "ai"), ("OpenCV", None, 0, "ai"), ("computer vision", None, 0, "ai"), ("NLP", r"NLP|natural language processing", 0, "ai"),
 ("LLMs", r"LLMs?|large language models?", 0, "ai"), ("transformers", r"transformer (?:models?|architectures?|networks?)|transformer-based", 0, "ai"),
 ("reinforcement learning", None, 0, "ai"), ("TensorRT", None, 0, "ai"), ("ONNX", None, 0, "ai"), ("MLOps", None, 0, "ai"),
 ("statistics", r"statistics|statistical", 0, "ai"), ("probability", None, 0, "ai"), ("linear algebra", None, 0, "ai"),
 ("Tableau", None, 0, "ai"), ("Power BI", None, 0, "ai"), ("Excel", r"Excel|Microsoft Excel", 1, "ai"),
 ("data visualization", None, 0, "ai"), ("A/B testing", r"A/B test(?:s|ing)?", 0, "ai"),
 # quant
 ("stochastic calculus", r"stochastic(?: calculus| processes)", 0, "quant"),
 ("options and derivatives", r"options pricing|options theory|derivatives pricing|derivatives", 0, "quant"),
 ("market making", r"market[- ]making", 0, "quant"), ("kdb+/q", r"kdb\+?|KDB\+?", 0, "quant"), ("backtesting", r"back-?test(?:ing|s)?", 0, "quant"),
 ("Monte Carlo", None, 0, "quant"),
]
GROUPS = {"lang": "Languages", "hw": "Digital design", "io": "Interfaces and RF", "board": "Boards and analog", "emb": "Embedded",
          "eda": "EDA tools", "sw": "Software", "ai": "AI and data", "quant": "Quant"}
# Things in a posting that can rule you out regardless of skills.
FLAGS = [
 ("us_citizen", "US citizens only", r"U\.?S\.? citizen(?:ship)?(?: is)? (?:is )?required|must be (?:a )?U\.?S\.? citizen|requires? U\.?S\.? citizenship|U\.?S\.? persons? (?:only|status)|ITAR|export[- ]control(?:led)? (?:regulations|laws|requirements)|eligible (?:to obtain|for) (?:a )?(?:U\.?S\.? )?(?:government )?security clearance|citizenship is required"),
 ("clearance", "Security clearance", r"(?:active|current|obtain|maintain|hold)(?: an?)? (?:secret|top secret|TS/SCI|security) clearance|clearance is required|DoD clearance"),
 ("no_sponsor", "No visa sponsorship", r"(?:will not|won't|cannot|can't|unable to|does not|do not|no) (?:provide |offer )?(?:visa |immigration )?sponsor(?:ship)?|without (?:the need for )?(?:current or future )?(?:visa )?sponsorship|not eligible for (?:visa )?sponsorship"),
 ("french", "French required", r"bilingu(?:al|ism)[^.]{0,40}(?:French|français)|(?:French|français)[^.]{0,30}(?:required|essential|mandatory|obligatoire)|fluen(?:t|cy) in (?:English and )?French"),
 ("gpa", "Minimum GPA", r"(?:minimum|min\.?|at least)(?: a)?(?: cumulative)? GPA(?: of)? \d\.\d|GPA of \d\.\d or (?:higher|above|better)|\d\.\d\+? GPA"),
]
STOP = ("US USA EEO EEOC PTO GPA HR CEO CTO CFO VP OR IT FAQ ID LLC INC NA TBD NYC SF LA CA ON BC QC AB TX WA NY MA NJ UK EU "
        "EST PST ET PT CV PDF URL AM PM ADA OFCCP VEVRAA LGBTQ DEI ESG KPI ROI YES NO THE AND FOR WITH YOU OUR ALL NOT ARE MBA "
        "PHD MS BS BA BSC MSC BASC BENG MENG AI ML IP R&D RD AS OF TO IN AT BY IS BE WE HQ API APIS SDK UI UX QA OS PC TV NOTE ETC "
        "USD CAD LLM").split()

def pattern(label, rx):
    return rx if rx else re.escape(label)

out = {"version": 1, "boundary": [r"(?<![\w+#.])(?:", r")(?![\w+#])"],
       "terms": [[label, pattern(label, rx), cs, grp] for label, rx, cs, grp in TERMS],
       "groups": GROUPS, "flags": [[k, label, rx] for k, label, rx in FLAGS], "stop": STOP}
for label, rx, cs, grp in out["terms"]:
    re.compile(out["boundary"][0] + rx + out["boundary"][1], 0 if cs else re.I)
for k, label, rx in out["flags"]:
    re.compile(rx, re.I)
path = Path(__file__).resolve().parent.parent / "docs" / "keywords.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
print(f"wrote {path} with {len(out['terms'])} terms, {len(out['flags'])} flags")

#include "Config.h"


Config::Config()
{
	// TODO: reasonable defaults
	test_host[0] = '\0';
	test_port = 0;
	test_com = 0;
	test_pump_addr = 0;

	little_endian = false;
	size_includes_header = false;
	logical_com1 = false;

	timeout_ms = 0;
	log_path[0] = '\0';
}


/*
 * Load the config from a file path
 */
int Config::Load(const char* path)
{
	std::ifstream f(path);
	if (!f) return -1;
	std::string line;
	size_t pos;

	while (std::getline(f, line))
	{
		if ((pos = line.find('=')) == std::string::npos)
			continue;

		std::string key = line.substr(0, pos),
					value = line.substr(pos+1);

#define ENTRY_STR(KEY, N)	if (key == #KEY) strncpy(KEY, value.c_str(), N)
#define ENTRY_NUM(KEY, T)	if (key == #KEY) KEY = (T)std::stoi(value)
		/*
		 * Switch on the actual values 
		 */
		ENTRY_STR(test_host, MAX_HOST_LEN - 1);
		else ENTRY_NUM(test_port, ushort);
		else ENTRY_NUM(test_com, int);
		else ENTRY_NUM(test_pump_addr, int);
		else ENTRY_NUM(little_endian, bool);
		else ENTRY_NUM(size_includes_header, bool);
		else ENTRY_NUM(logical_com1, ushort);
		else ENTRY_NUM(timeout_ms, ushort);
		else ENTRY_STR(log_path, MAX_PATH - 1);
	}
	test_host[MAX_HOST_LEN - 1] = '\0';
	log_path[MAX_PATH - 1] = '\0';

#undef ENTRY_STR
#undef ENTRY_NUM
	return 0;
}


/* static */ Config Config::FromArgs(int argc, char** argv)
{
	Config cfg{};
	for (int i = 1; i < argc; i++)
	{
		if (strcmp(argv[i], "-f") == 0)
		{
			if (i + 1 >= argc) 
				FATAL("-f flag requires a config file path");

			if (cfg.Load(argv[++i]) != 0)
				FATAL("Failed to load config file: %s", argv[i]);
			break;
		}
	}
	return cfg;
}


void Config::Print() const
{
	printf(
		"Config:\n"
		"  test_host:            %s\n"
		"  test_port:            %hu\n"
		"  test_com:             %d\n"
		"  test_pump_addr:       %d\n"
		"  little_endian:        %s\n"
		"  size_includes_header: %s\n"
		"  logical_com1:         %s\n"
		"  timeout_ms:           %hu\n"
		"  log_path:             %s\n",
		test_host,
		test_port,
		test_com,
		test_pump_addr,
		BOOLYN(little_endian),
		BOOLYN(size_includes_header),
		BOOLYN(logical_com1),
		timeout_ms,
		log_path);
}

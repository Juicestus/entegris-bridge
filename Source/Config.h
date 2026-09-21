#pragma once

#include "Util.h"

#define MAX_HOST_LEN	256

struct Config
{
	char	test_host[MAX_HOST_LEN];
	ushort	test_port;
	int		test_com;
	int		test_pump_addr;

	/* 1 = little endian, 0 = big endian */
	bool	little_endian;
	/* 1 = size includes header, 0 = size excludes header */
	bool	size_includes_header;

	/* 1 = COM1 is logical serial 1, 0 = logical serial 0*/
	ushort	logical_com1;

	ushort	timeout_ms;
	char	log_path[MAX_PATH];

	/* 
	 * Default config 
	 */
	Config();
	/*
	 * Load the config from a file path
     */
	int Load(const char* path);

	static Config FromArgs(int argc, char** argv);

	void Print() const;
};

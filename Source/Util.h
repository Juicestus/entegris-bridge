#pragma once

#ifdef _WIN32
#define _CRT_SECURE_NO_WARNINGS	// TODO: compat layer
#endif

#include <iostream>
#include <fstream>
#include <string>

#include <cstdio>
#include <cstdint>
#include <cassert>
#include <cstring>

#ifdef _WIN32

#define _WINSOCK_DEPRECATED_NO_WARNINGS
#include <winsock2.h>
#include <ws2tcpip.h>
#include <Windows.h>
#pragma comment(lib, "ws2_32.lib")

#else
/* unix specific */
#endif

using uint = unsigned int;
using ushort = unsigned short;

#define FATAL(FMT, ...)	fprintf(stderr, FMT "\n", __VA_ARGS__)
#define ARR_LEN(A)		(sizeof(A) / sizeof(*A))

#define BOOLTF(BOOL)	( (BOOL) ? "TRUE" : "FALSE" )		// bool -> true/false string
#define BOOLYN(BOOL)	( (BOOL) ? "YES" : "NO" )			// bool -> yes/no string 

// serial layer is LE per CommSpec, NCS header assumed LE too, so these are
// no-ops on an LE host unless the config says the NCS header is BE
// TODO: make portable
__forceinline uint16_t Wire16(uint16_t v, bool be) 
{ 
	return be ? _byteswap_ushort(v) : v; 
}
__forceinline uint32_t Wire32(uint32_t v, bool be) 
{ 
	return be ? (uint32_t)_byteswap_ulong(v) : v; 
}


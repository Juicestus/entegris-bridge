#pragma once

#ifdef _WIN32
#define _CRT_SECURE_NO_WARNINGS	// TODO: compat layer
#endif

#include <iostream>
#include <fstream>
#include <string>
#include <algorithm>
#include <array>
#include <string_view>

#include <cstdio>
#include <cstdint>
#include <cassert>
#include <cstring>
#include <cstddef>

#ifdef _WIN32

#define _WINSOCK_DEPRECATED_NO_WARNINGS
#define NOMINMAX

#include <winsock2.h>
#include <ws2tcpip.h>
#include <Windows.h>
#pragma comment(lib, "ws2_32.lib")

using ssize_t = SSIZE_T;

#else

#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <limits.h>

using SOCKET = int;
#define INVALID_SOCKET	(-1)
#define SOCKET_ERROR	(-1)
#define SD_SEND			SHUT_WR
#define MAX_PATH		PATH_MAX
#define closesocket		close
#define WSAGetLastError()	errno
// connect() in progress: Windows returns WSAEWOULDBLOCK, POSIX returns EINPROGRESS
#define WSAEWOULDBLOCK	EINPROGRESS
#define WSAETIMEDOUT	ETIMEDOUT

using byte = uint8_t;

#endif

using uint = unsigned int;
using ushort = unsigned short;

#define FATAL(FMT, ...)	fprintf(stderr, FMT "\n", ##__VA_ARGS__)
#define ARR_LEN(A)		(sizeof(A) / sizeof(*A))

#ifdef _WIN32
#define SAFE_STRCPY(DST, CAP, SRC)	strncpy_s(DST, CAP, SRC, _TRUNCATE)
#else
#define SAFE_STRCPY(DST, CAP, SRC)	do { strncpy(DST, SRC, (CAP) - 1); (DST)[(CAP) - 1] = '\0'; } while (0)
#endif

#define BOOLTF(BOOL)	( (BOOL) ? "TRUE" : "FALSE" )		// bool -> true/false string
#define BOOLYN(BOOL)	( (BOOL) ? "YES" : "NO" )			// bool -> yes/no string


#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__)
// should never err 
#error "Big-endian hosts are not supported"		
#endif


#ifndef FORCEINLINE
#if defined(_MSC_VER)
#define FORCEINLINE		__forceinline
#elif defined(__GNUC__) || defined(__clang__)
#define FORCEINLINE		inline __attribute__((always_inline))
#else
#define FORCEINLINE		inline
#endif
#endif

#if defined(_MSC_VER)
#define BSWAP16(x)		_byteswap_ushort(x)
#define BSWAP32(x)		((uint32_t)_byteswap_ulong(x))
#elif defined(__GNUC__) || defined(__clang__)
#define BSWAP16(x)		__builtin_bswap16(x)
#define BSWAP32(x)		__builtin_bswap32(x)
#else
#define BSWAP16(x)		((uint16_t)(((x) >> 8) | ((x) << 8)))
#define BSWAP32(x)		(((x) >> 24) | (((x) >> 8) & 0x0000FF00u) | \
							 (((x) << 8) & 0x00FF0000u) | ((x) << 24))
#endif

template <size_t N>
constexpr std::array<char, N> __MakeNameBuffer(const char(&s)[N])
{
	std::array<char, N> buf{};
	for (size_t i = 0; i < N; ++i)
		buf[i] = (s[i] == ',' || s[i] == ' ') ? '\0' : s[i];
	return buf;
}

template <size_t Count, size_t N>
constexpr std::array<const char*, Count> __MakeNameTable(const std::array<char, N>& buf)
{
	std::array<const char*, Count> out{};
	size_t idx = 0;
	for (size_t i = 0; i < N && idx < Count; ++i)
		if (buf[i] != '\0' && (i == 0 || buf[i - 1] == '\0'))
			out[idx++] = &buf[i];
	return out;
}

#define DEFINE_REFLECTIVE_ENUM(EnumName, Type, ...)									\
	enum class EnumName : Type { __VA_ARGS__, COUNT_ };								\
	inline constexpr auto EnumName##_buffer = __MakeNameBuffer(#__VA_ARGS__);		\
	inline constexpr auto EnumName##_names = __MakeNameTable<static_cast<size_t>	\
			(EnumName::COUNT_)>(EnumName##_buffer);									\
	constexpr const char* ToString(EnumName e)										\
	{																				\
		auto i = static_cast<size_t>(e);											\
		return i < EnumName##_names.size() ? EnumName##_names[i] : "?";				\
	}

/**
 * Hex / ascii dump
 */
void PrintBytes(const char* label, const byte* buf, size_t len);



